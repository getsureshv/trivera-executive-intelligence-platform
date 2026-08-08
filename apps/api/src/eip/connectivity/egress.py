"""Direct-mode egress guard: endpoint validation, DNS resolution, rebinding prevention."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "DenialCode",
    "DnsResolution",
    "EgressDecision",
    "EgressValidator",
]


class DenialCode(StrEnum):
    """Non-secret, machine-readable denial codes."""

    MALFORMED_ENDPOINT = "malformed_endpoint"
    DNS_ERROR = "dns_error"
    EMPTY_DNS_ANSWERS = "empty_dns_answers"
    MIXED_SAFE_UNSAFE = "mixed_safe_unsafe"
    IPV4_MAPPED_IPV6 = "ipv4_mapped_ipv6"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    UNSPECIFIED = "unspecified"
    RESERVED = "reserved"
    PRIVATE = "private"
    CLOUD_METADATA = "cloud_metadata"
    NOT_IN_ALLOWLIST = "not_in_allowlist"


@dataclass(frozen=True)
class DnsResolution:
    """DNS resolution result with all resolved addresses."""

    addresses: tuple[str, ...]  # IPv4 or IPv6 strings
    error: str | None = None

    def __post_init__(self) -> None:
        if self.error and self.addresses:
            raise ValueError("DnsResolution must have either error or addresses, not both")


@dataclass(frozen=True)
class EgressDecision:
    """Egress validation decision."""

    allowed: bool
    selected_address: str | None = None
    denial_code: DenialCode | None = None
    details: str = ""

    def __post_init__(self) -> None:
        if self.allowed:
            if self.denial_code is not None or self.selected_address is None:
                raise ValueError("Allowed decision must have selected_address and no denial_code")
        else:
            if self.denial_code is None or self.selected_address is not None:
                raise ValueError("Denied decision must have denial_code and no selected_address")


class Resolver(Protocol):
    """Injected DNS resolver for testing determinism."""

    def resolve(self, hostname: str) -> DnsResolution:
        """Resolve hostname to addresses or return error."""
        ...


def _classify_address(
    addr_str: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address | None, DenialCode | None]:
    """Classify an IP address, returning the address object and any denial code."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return None, DenialCode.MALFORMED_ENDPOINT

    # Check for IPv4-mapped IPv6 addresses
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr, DenialCode.IPV4_MAPPED_IPV6

    # Give the well-known metadata endpoint its stable, specific code before
    # the broader link-local classification.
    if str(addr) == "169.254.169.254":
        return addr, DenialCode.CLOUD_METADATA

    # Check unsafe categories
    if addr.is_loopback:
        return addr, DenialCode.LOOPBACK
    if addr.is_link_local:
        return addr, DenialCode.LINK_LOCAL
    if addr.is_multicast:
        return addr, DenialCode.MULTICAST
    if addr.is_unspecified:
        return addr, DenialCode.UNSPECIFIED
    if addr.is_reserved:
        return addr, DenialCode.RESERVED
    if addr.is_private:
        return addr, DenialCode.PRIVATE

    return addr, None


def _parse_endpoint(endpoint: str) -> tuple[str, int | None] | None:
    """Parse endpoint into (host, port) or None if malformed."""
    if not endpoint or not isinstance(endpoint, str) or endpoint != endpoint.strip():
        return None

    # Handle [IPv6]:port format
    if endpoint.startswith("["):
        if "]:" in endpoint:
            try:
                host_part, port_part = endpoint.rsplit(":", 1)
                host = host_part[1:-1]  # Remove [ and ]
                parsed_host = ipaddress.ip_address(host)
                if not isinstance(parsed_host, ipaddress.IPv6Address):
                    return None
                port = int(port_part)
                if 0 < port < 65536:
                    return (host, port)
            except (ValueError, IndexError):
                return None
        elif endpoint.endswith("]"):
            try:
                host = endpoint[1:-1]
                ipaddress.ip_address(host)  # Validate it's an IPv6
                return (host, None)
            except ValueError:
                return None
        else:
            return None

    # An unbracketed IPv6 literal is valid only without a port.
    try:
        ipaddress.ip_address(endpoint)
    except ValueError:
        pass
    else:
        return endpoint, None

    if endpoint.count(":") == 1:
        host, port_part = endpoint.split(":", 1)
        if not host:
            return None
        try:
            port = int(port_part)
        except ValueError:
            return None
        return (host, port) if 0 < port < 65536 else None
    if ":" in endpoint:
        return None

    hostname = re.fullmatch(
        r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
        endpoint,
    )
    return (endpoint, None) if hostname else None


class EgressValidator:
    """Egress policy validator using dependency injection for testing."""

    def __init__(
        self,
        resolver: Resolver,
        allowlist: Sequence[str] | None = None,
    ):
        """Initialize validator with resolver and optional allowlist.

        Args:
            resolver: Injected DNS resolver
            allowlist: Optional list of CIDR networks (e.g., ["10.0.0.0/8"])
        """
        self.resolver = resolver
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for cidr in allowlist or ():
            try:
                networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError as exc:
                msg = f"Invalid CIDR in allowlist: {cidr}"
                raise ValueError(msg) from exc
        self._allowlist = tuple(networks)

    def _is_address_allowed(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Check if address is in allowlist (if allowlist exists), or allowed by default."""
        if not self._allowlist:
            return True
        return any(addr in network for network in self._allowlist)

    def validate_endpoint(self, endpoint: str) -> EgressDecision:
        """Validate endpoint: parse, resolve DNS, classify addresses, check allowlist."""

        # Parse endpoint
        parsed = _parse_endpoint(endpoint)
        if not parsed:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MALFORMED_ENDPOINT,
                details="Endpoint could not be parsed",
            )

        host, _port = parsed

        # Try to parse as direct IP first
        addr_obj, denial = _classify_address(host)
        if addr_obj is not None:
            # Check allowlist: if in allowlist, it's authorized as controlled exception
            # If not in allowlist and unsafe, fail; if not in allowlist and safe, allow
            if self._allowlist:
                in_allowlist = any(addr_obj in network for network in self._allowlist)
                if not in_allowlist:
                    # Address not in allowlist; if unsafe, deny; if safe, allow
                    if denial is not None:
                        return EgressDecision(
                            allowed=False,
                            denial_code=denial,
                            details=f"Direct IP {host} unsafe and not in allowlist",
                        )
                    # Safe but not in allowlist; deny per policy
                    return EgressDecision(
                        allowed=False,
                        denial_code=DenialCode.NOT_IN_ALLOWLIST,
                        details=f"IP {host} not in allowlist",
                    )
                # In allowlist; authorize (override unsafe if necessary)
                return EgressDecision(allowed=True, selected_address=host)
            else:
                # No allowlist; deny if unsafe, allow if safe
                if denial is not None:
                    return EgressDecision(
                        allowed=False, denial_code=denial, details=f"Direct IP: {host}"
                    )
                return EgressDecision(allowed=True, selected_address=host)

        # Resolve hostname via DNS
        resolution = self.resolver.resolve(host)
        if resolution.error:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.DNS_ERROR,
                details="DNS resolution failed",
            )

        if not resolution.addresses:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.EMPTY_DNS_ANSWERS,
                details=f"No DNS answers for {host}",
            )

        # Classify all addresses; fail closed on malformed
        classified: list[
            tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None, DenialCode | None]
        ] = []
        for addr_str in resolution.addresses:
            addr_obj, denial = _classify_address(addr_str)
            classified.append((addr_str, addr_obj, denial))

        # Check for malformed addresses (fail closed)
        malformed = [addr_str for addr_str, addr_obj, denial in classified if addr_obj is None]
        if malformed:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MALFORMED_ENDPOINT,
                details=f"DNS returned malformed addresses: {malformed}",
            )

        # Separate safe from unsafe
        denials = [d for _, _, d in classified if d is not None]
        safes = [addr for _, addr, d in classified if d is None]

        if denials and safes:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MIXED_SAFE_UNSAFE,
                details=f"DNS returned mixed safe/unsafe addresses for {host}",
            )

        addresses = tuple(addr for _, addr, _ in classified if addr is not None)
        if self._allowlist and not all(self._is_address_allowed(addr) for addr in addresses):
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.NOT_IN_ALLOWLIST,
                details="At least one DNS answer is outside the allowlist",
            )

        if denials:
            # Every answer must be explicitly allowlisted for a controlled exception.
            if self._allowlist:
                return EgressDecision(allowed=True, selected_address=str(addresses[0]))
            # Not in allowlist or no allowlist; deny
            return EgressDecision(
                allowed=False,
                denial_code=denials[0],
                details=f"All DNS answers are unsafe ({host})",
            )

        # All safe; select first one and validate against allowlist
        selected = classified[0][1]
        selected_str = str(selected)

        return EgressDecision(allowed=True, selected_address=selected_str)

    def validate_peer_pre_connection(self, endpoint: str, peer_address: str) -> EgressDecision:
        """Validate peer address immediately before connection (DNS rebinding prevention).

        Ensures the peer address matches one of the currently-resolved DNS answers.
        """
        parsed = _parse_endpoint(endpoint)
        if not parsed:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MALFORMED_ENDPOINT,
                details="Endpoint could not be parsed",
            )

        host, _port = parsed

        # Try direct IP match first: classify peer_address if host matches
        if host == peer_address:
            addr_obj, denial = _classify_address(peer_address)
            if addr_obj is None:
                return EgressDecision(
                    allowed=False,
                    denial_code=DenialCode.MALFORMED_ENDPOINT,
                    details=f"Peer address {peer_address} is malformed",
                )
            # Check allowlist: if it exists and peer not in it, deny (even if safe)
            if self._allowlist:
                in_allowlist = any(addr_obj in network for network in self._allowlist)
                if not in_allowlist:
                    # Peer not in allowlist; deny with appropriate code
                    if denial is not None:
                        return EgressDecision(
                            allowed=False,
                            denial_code=denial,
                            details=f"Peer {peer_address} unsafe and not in allowlist",
                        )
                    return EgressDecision(
                        allowed=False,
                        denial_code=DenialCode.NOT_IN_ALLOWLIST,
                        details=f"Peer {peer_address} not in allowlist",
                    )
                # In allowlist; authorize (override unsafe if necessary)
                return EgressDecision(allowed=True, selected_address=peer_address)
            else:
                # No allowlist; deny if unsafe, allow if safe
                if denial is not None:
                    return EgressDecision(
                        allowed=False,
                        denial_code=denial,
                        details=f"Peer {peer_address} is unsafe",
                    )
                return EgressDecision(allowed=True, selected_address=peer_address)

        # Resolve and check if peer is in current answers
        resolution = self.resolver.resolve(host)
        if resolution.error:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.DNS_ERROR,
                details="DNS resolution failed during peer validation",
            )

        if not resolution.addresses:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.EMPTY_DNS_ANSWERS,
                details=f"No current DNS answers for {host}",
            )

        # Classify all addresses from DNS; fail closed on malformed
        classified: list[
            tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None, DenialCode | None]
        ] = []
        for addr_str in resolution.addresses:
            addr_obj, denial = _classify_address(addr_str)
            classified.append((addr_str, addr_obj, denial))

        # Fail closed on malformed addresses
        malformed = [addr_str for addr_str, addr_obj, denial in classified if addr_obj is None]
        if malformed:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MALFORMED_ENDPOINT,
                details=f"DNS returned malformed addresses: {malformed}",
            )

        # Check for mixed safe/unsafe
        denials = [d for _, _, d in classified if d is not None]
        safes = [addr for _, addr, d in classified if d is None]

        if denials and safes:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.MIXED_SAFE_UNSAFE,
                details=f"DNS returned mixed safe/unsafe addresses for {host}",
            )

        addresses = tuple(addr for _, addr, _ in classified if addr is not None)
        if self._allowlist and not all(self._is_address_allowed(addr) for addr in addresses):
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.NOT_IN_ALLOWLIST,
                details="At least one current DNS answer is outside the allowlist",
            )

        # Check if peer is in current answers
        if peer_address not in resolution.addresses:
            return EgressDecision(
                allowed=False,
                denial_code=DenialCode.DNS_ERROR,
                details=(
                    f"Peer address {peer_address} does not match current DNS answers for {host}"
                ),
            )

        # Peer is in answers; find its classification
        peer_obj = None
        peer_denial = None
        for addr_str, addr_obj, denial in classified:
            if addr_str == peer_address:
                peer_obj = addr_obj
                peer_denial = denial
                break

        # Peer is guaranteed to be classified since we checked for malformed/mixed above
        # Check allowlist: if it exists and peer not in it, deny (even if safe)
        if self._allowlist:
            in_allowlist = any(peer_obj in network for network in self._allowlist)
            if not in_allowlist:
                # Peer not in allowlist; deny with appropriate code
                if peer_denial is not None:
                    return EgressDecision(
                        allowed=False,
                        denial_code=peer_denial,
                        details=f"Resolved peer {peer_address} unsafe and not in allowlist",
                    )
                return EgressDecision(
                    allowed=False,
                    denial_code=DenialCode.NOT_IN_ALLOWLIST,
                    details=f"Resolved peer {peer_address} not in allowlist",
                )
            # In allowlist; authorize (override unsafe if necessary)
            return EgressDecision(allowed=True, selected_address=peer_address)
        else:
            # No allowlist; deny if unsafe, allow if safe
            if peer_denial is not None:
                return EgressDecision(
                    allowed=False,
                    denial_code=peer_denial,
                    details=f"Resolved peer {peer_address} is unsafe",
                )
            return EgressDecision(allowed=True, selected_address=peer_address)
