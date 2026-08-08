"""Egress policy validation tests with deterministic DNS injection."""

import pytest

from eip.connectivity.egress import (
    DenialCode,
    DnsResolution,
    EgressDecision,
    EgressValidator,
)


class DeterministicResolver:
    """Deterministic test resolver with configurable responses."""

    def __init__(self, responses: dict[str, DnsResolution]):
        self.responses = responses
        self.call_log: list[str] = []

    def resolve(self, hostname: str) -> DnsResolution:
        self.call_log.append(hostname)
        return self.responses.get(hostname, DnsResolution(addresses=(), error="Not configured"))


class TestEndpointParsing:
    """Test endpoint parsing logic."""

    def test_parse_simple_hostname(self):
        resolver = DeterministicResolver({"example.com": DnsResolution(addresses=("8.8.8.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("example.com")
        assert decision.allowed is True
        assert decision.selected_address == "8.8.8.1"

    def test_parse_hostname_with_port(self):
        resolver = DeterministicResolver({"example.com": DnsResolution(addresses=("8.8.8.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("example.com:5432")
        assert decision.allowed is True

    def test_parse_ipv4_direct(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("8.8.8.1")
        assert decision.allowed is True
        assert decision.selected_address == "8.8.8.1"

    def test_parse_ipv4_with_port(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("8.8.8.1:5432")
        assert decision.allowed is True

    def test_parse_ipv6_direct(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("2606:4700::1")
        assert decision.allowed is True

    def test_parse_ipv6_with_port_bracketed(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("[2606:4700::1]:5432")
        assert decision.allowed is True

    def test_parse_malformed_endpoint_empty(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT

    def test_parse_malformed_endpoint_invalid_port(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("example.com:99999")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT

    def test_parse_malformed_endpoint_non_numeric_port(self):
        resolver = DeterministicResolver({})
        decision = EgressValidator(resolver).validate_endpoint("example.com:postgres")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT
        assert resolver.call_log == []

    def test_parse_malformed_ipv6_bracket(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("[gggg::1]:5432")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT


class TestDnsResolution:
    """Test DNS resolution and error handling."""

    def test_dns_error(self):
        resolver = DeterministicResolver(
            {"notexist.test": DnsResolution(addresses=(), error="NXDOMAIN")}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("notexist.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.DNS_ERROR

    def test_empty_dns_answers(self):
        resolver = DeterministicResolver({"empty.test": DnsResolution(addresses=())})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("empty.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.EMPTY_DNS_ANSWERS

    def test_malformed_dns_answer_fails_closed(self):
        resolver = DeterministicResolver(
            {"bad.test": DnsResolution(addresses=("8.8.8.8", "not-an-ip"))}
        )
        decision = EgressValidator(resolver).validate_endpoint("bad.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT

    def test_dns_multiple_safe_addresses(self):
        resolver = DeterministicResolver(
            {"multi.test": DnsResolution(addresses=("8.8.8.1", "8.8.8.2", "8.8.8.3"))}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("multi.test")
        assert decision.allowed is True
        assert decision.selected_address == "8.8.8.1"  # First safe one


class TestAddressClassification:
    """Test IP address classification (safe vs unsafe)."""

    def test_loopback_ipv4(self):
        resolver = DeterministicResolver({"loopback.test": DnsResolution(addresses=("127.0.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("loopback.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.LOOPBACK

    def test_loopback_ipv6(self):
        resolver = DeterministicResolver({"loopback.test": DnsResolution(addresses=("::1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("loopback.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.LOOPBACK

    def test_link_local_ipv4(self):
        resolver = DeterministicResolver({"link.test": DnsResolution(addresses=("169.254.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("link.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.LINK_LOCAL

    def test_link_local_ipv6(self):
        resolver = DeterministicResolver({"link.test": DnsResolution(addresses=("fe80::1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("link.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.LINK_LOCAL

    def test_multicast_ipv4(self):
        resolver = DeterministicResolver({"multi.test": DnsResolution(addresses=("224.0.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("multi.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MULTICAST

    def test_multicast_ipv6(self):
        resolver = DeterministicResolver({"multi.test": DnsResolution(addresses=("ff00::1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("multi.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MULTICAST

    def test_unspecified_ipv4(self):
        resolver = DeterministicResolver(
            {"any.test": DnsResolution(addresses=("0.0.0.0",))}  # noqa: S104
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("any.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.UNSPECIFIED

    def test_unspecified_ipv6(self):
        resolver = DeterministicResolver({"any.test": DnsResolution(addresses=("::",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("any.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.UNSPECIFIED

    def test_reserved_ipv4(self):
        resolver = DeterministicResolver({"reserved.test": DnsResolution(addresses=("240.0.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("reserved.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.RESERVED

    def test_private_rfc1918(self):
        resolver = DeterministicResolver({"private.test": DnsResolution(addresses=("10.0.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("private.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.PRIVATE

    def test_private_172_range(self):
        resolver = DeterministicResolver({"private.test": DnsResolution(addresses=("172.16.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("private.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.PRIVATE

    def test_private_192_range(self):
        resolver = DeterministicResolver(
            {"private.test": DnsResolution(addresses=("192.168.0.1",))}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("private.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.PRIVATE

    def test_cloud_metadata_aws(self):
        resolver = DeterministicResolver(
            {"metadata.test": DnsResolution(addresses=("169.254.169.254",))}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("metadata.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.CLOUD_METADATA

    def test_ipv4_mapped_ipv6(self):
        resolver = DeterministicResolver(
            {"mapped.test": DnsResolution(addresses=("::ffff:8.8.8.1",))}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("mapped.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.IPV4_MAPPED_IPV6


class TestMixedSafeUnsafe:
    """Test fail-closed behavior on mixed DNS results."""

    def test_mixed_safe_and_unsafe(self):
        resolver = DeterministicResolver(
            {
                "mixed.test": DnsResolution(
                    addresses=("8.8.8.1", "127.0.0.1")  # One safe, one loopback
                )
            }
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("mixed.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MIXED_SAFE_UNSAFE

    def test_mixed_safe_and_private(self):
        resolver = DeterministicResolver(
            {
                "mixed.test": DnsResolution(
                    addresses=("8.8.8.1", "10.0.0.1")  # One safe, one private
                )
            }
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_endpoint("mixed.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MIXED_SAFE_UNSAFE


class TestAllowlist:
    """Test immutable IP allowlist."""

    def test_allowlist_allows_permitted_cidr(self):
        resolver = DeterministicResolver({"allowed.test": DnsResolution(addresses=("1.1.1.5",))})
        validator = EgressValidator(resolver, allowlist=["1.1.1.0/24"])
        decision = validator.validate_endpoint("allowed.test")
        assert decision.allowed is True

    def test_allowlist_blocks_unpermitted_cidr(self):
        resolver = DeterministicResolver({"blocked.test": DnsResolution(addresses=("8.8.8.1",))})
        validator = EgressValidator(resolver, allowlist=["1.1.1.0/24"])
        decision = validator.validate_endpoint("blocked.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.NOT_IN_ALLOWLIST

    def test_allowlist_ipv6(self):
        resolver = DeterministicResolver({"ipv6.test": DnsResolution(addresses=("2606:4700::1",))})
        validator = EgressValidator(resolver, allowlist=["2606:4700::/32"])
        decision = validator.validate_endpoint("ipv6.test")
        assert decision.allowed is True

    def test_direct_ip_checked_against_allowlist(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver, allowlist=["1.1.1.0/24"])
        decision = validator.validate_endpoint("1.1.1.1")
        assert decision.allowed is True

    def test_direct_ip_blocked_by_allowlist(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver, allowlist=["1.1.1.0/24"])
        decision = validator.validate_endpoint("8.8.8.1")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.NOT_IN_ALLOWLIST

    def test_invalid_cidr_raises(self):
        resolver = DeterministicResolver({})
        with pytest.raises(ValueError, match="Invalid CIDR"):
            EgressValidator(resolver, allowlist=["not-a-cidr"])

    def test_allowlist_is_a_controlled_private_network_exception(self):
        resolver = DeterministicResolver(
            {"private.test": DnsResolution(addresses=("10.20.30.40",))}
        )
        validator = EgressValidator(resolver, allowlist=["10.20.0.0/16"])
        decision = validator.validate_endpoint("private.test")
        assert decision.allowed is True

    def test_every_dns_answer_must_be_allowlisted(self):
        resolver = DeterministicResolver(
            {"private.test": DnsResolution(addresses=("10.20.30.40", "10.30.40.50"))}
        )
        validator = EgressValidator(resolver, allowlist=["10.20.0.0/16"])
        decision = validator.validate_endpoint("private.test")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.NOT_IN_ALLOWLIST


class TestRebindingPrevention:
    """Test DNS rebinding attack prevention."""

    def test_rebinding_peer_matches_current_answer(self):
        resolver = DeterministicResolver(
            {"rebind.test": DnsResolution(addresses=("1.1.1.1", "1.1.1.2"))}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_peer_pre_connection("rebind.test", "1.1.1.1")
        assert decision.allowed is True
        assert decision.selected_address == "1.1.1.1"

    def test_rebinding_peer_not_in_current_answers(self):
        resolver = DeterministicResolver({"rebind.test": DnsResolution(addresses=("1.1.1.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_peer_pre_connection("rebind.test", "1.1.1.99")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.DNS_ERROR

    def test_rebinding_direct_ip_matches(self):
        resolver = DeterministicResolver({})
        validator = EgressValidator(resolver)
        decision = validator.validate_peer_pre_connection("1.1.1.1", "1.1.1.1")
        assert decision.allowed is True

    def test_rebinding_peer_is_unsafe(self):
        resolver = DeterministicResolver({"rebind.test": DnsResolution(addresses=("10.0.0.1",))})
        validator = EgressValidator(resolver)
        decision = validator.validate_peer_pre_connection("rebind.test", "10.0.0.1")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.PRIVATE

    def test_rebinding_dns_error_during_check(self):
        resolver = DeterministicResolver(
            {"rebind.test": DnsResolution(addresses=(), error="SERVFAIL")}
        )
        validator = EgressValidator(resolver)
        decision = validator.validate_peer_pre_connection("rebind.test", "1.1.1.1")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.DNS_ERROR

    def test_direct_private_peer_is_denied_without_allowlist(self):
        decision = EgressValidator(DeterministicResolver({})).validate_peer_pre_connection(
            "10.0.0.1", "10.0.0.1"
        )
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.PRIVATE

    def test_direct_private_peer_can_be_explicitly_allowlisted(self):
        validator = EgressValidator(DeterministicResolver({}), allowlist=["10.0.0.0/8"])
        decision = validator.validate_peer_pre_connection("10.0.0.1", "10.0.0.1")
        assert decision.allowed is True

    def test_rebinding_mixed_answers_fail_closed(self):
        resolver = DeterministicResolver(
            {"rebind.test": DnsResolution(addresses=("8.8.8.8", "127.0.0.1"))}
        )
        decision = EgressValidator(resolver).validate_peer_pre_connection("rebind.test", "8.8.8.8")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MIXED_SAFE_UNSAFE

    def test_rebinding_malformed_answer_fails_closed(self):
        resolver = DeterministicResolver(
            {"rebind.test": DnsResolution(addresses=("8.8.8.8", "invalid"))}
        )
        decision = EgressValidator(resolver).validate_peer_pre_connection("rebind.test", "8.8.8.8")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.MALFORMED_ENDPOINT

    def test_rebinding_requires_every_answer_in_allowlist(self):
        resolver = DeterministicResolver(
            {"rebind.test": DnsResolution(addresses=("10.20.30.40", "10.30.40.50"))}
        )
        validator = EgressValidator(resolver, allowlist=["10.20.0.0/16"])
        decision = validator.validate_peer_pre_connection("rebind.test", "10.20.30.40")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.NOT_IN_ALLOWLIST


class TestEgressDecisionInvariants:
    """Test EgressDecision value object contract."""

    def test_allowed_decision_must_have_address(self):
        with pytest.raises(ValueError, match="selected_address"):
            EgressDecision(allowed=True, denial_code=None, selected_address=None)

    def test_allowed_decision_must_not_have_denial_code(self):
        with pytest.raises(ValueError, match="denial_code"):
            EgressDecision(
                allowed=True,
                denial_code=DenialCode.LOOPBACK,
                selected_address="8.8.8.1",
            )

    def test_denied_decision_must_have_denial_code(self):
        with pytest.raises(ValueError, match="denial_code"):
            EgressDecision(allowed=False, denial_code=None, selected_address="8.8.8.1")

    def test_denied_decision_must_not_have_address(self):
        with pytest.raises(ValueError, match="selected_address"):
            EgressDecision(
                allowed=False,
                denial_code=DenialCode.LOOPBACK,
                selected_address="8.8.8.1",
            )

    def test_valid_allowed_decision(self):
        decision = EgressDecision(allowed=True, selected_address="8.8.8.1")
        assert decision.allowed is True
        assert decision.selected_address == "8.8.8.1"
        assert decision.denial_code is None

    def test_valid_denied_decision(self):
        decision = EgressDecision(allowed=False, denial_code=DenialCode.LOOPBACK, details="Details")
        assert decision.allowed is False
        assert decision.denial_code == DenialCode.LOOPBACK
        assert decision.selected_address is None


class TestDnsResolutionInvariants:
    """Test DnsResolution value object contract."""

    def test_cannot_have_both_error_and_addresses(self):
        with pytest.raises(ValueError, match="error or addresses"):
            DnsResolution(addresses=("8.8.8.1",), error="Error")

    def test_valid_with_addresses(self):
        res = DnsResolution(addresses=("8.8.8.1", "8.8.8.2"))
        assert res.addresses == ("8.8.8.1", "8.8.8.2")
        assert res.error is None

    def test_valid_with_error(self):
        res = DnsResolution(addresses=(), error="NXDOMAIN")
        assert res.addresses == ()
        assert res.error == "NXDOMAIN"
