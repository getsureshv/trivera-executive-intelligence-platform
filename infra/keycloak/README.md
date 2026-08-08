# Local identity provider (Keycloak)

Two realms, imported once at container start by `start-dev --import-realm`.

```bash
docker compose -f infra/docker-compose.yml --profile oidc up -d --wait keycloak
```

Everything here is local-only. The user password is a fixed literal because these
realms exist solely to be attacked by
`apps/api/tests/security/test_oidc_keycloak.py`; they authenticate nothing real.

Realm JSON files may not contain comment keys — Keycloak's importer rejects any
field it does not recognise, including `$comment` — so the reasoning lives here.

## Why a container rather than a hand-configured provider

Phase 1A verified the OIDC adapter against in-process RSA keys and a local JWKS
server. That proved the cryptography but not the protocol: discovery,
realm-scoped issuers, Keycloak's `aud` behaviour, and live key rotation were all
untested. That was gap **G13**. A hand-clicked realm would have closed it for one
machine; an imported one closes it for anybody who runs the compose file.

## `eip-test` — the provider under test

| Setting | Why |
| --- | --- |
| `directAccessGrantsEnabled` | Lets a test obtain a **genuine** token from the IdP in one request, instead of driving a browser redirect flow. |
| `oidc-audience-mapper` → `eip-api` | Keycloak's default `aud` is `account`; the API requires `aud == eip-api`. Without the mapper the suite would have to relax the audience check — one of the things it exists to prove. |
| Fixed user `id`s | Keycloak uses the user id as the `sub` claim. Pinning it lets the seeded `app_user` row reference the subject without a lookup, so the membership test is deterministic. |
| `ada` and `orphan` | `orphan` authenticates successfully and belongs to no tenant. Authenticated is not authorized, and that needs a real token to demonstrate. |

## `eip-other` — the second issuer

Exists for one assertion: a token that is cryptographically perfect, unexpired
and correctly audienced, but **issued by a different issuer with different
keys**, must be rejected.

Pointing the verifier at a fabricated issuer string would only prove that a
string comparison works. A real second issuer, with its own real signing keys at
its own real JWKS endpoint, is the actual attack — an attacker who controls *an*
identity provider must not thereby control *this* one.

## Signing keys

No key material is committed. The suite generates an RSA key at run time and
registers it through the Keycloak admin API, so Keycloak genuinely publishes it
in its JWKS. That is what makes the negative cases exact: an expired or
wrong-audience token can be signed by a key the provider really does advertise,
leaving the property under test as the only difference from a valid token.

Rotation is forced the same way — a second key provider at a higher priority —
so the rotation test observes a real `kid` change rather than a simulated one.

## Users need `firstName` and `lastName`

Not cosmetic. Keycloak's default `VERIFY_PROFILE` required action fires when a
profile is incomplete, and a direct grant for such a user fails with
`invalid_grant: Account is not fully set up` — which reads like a credential
problem and is not one.
