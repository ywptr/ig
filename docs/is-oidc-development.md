# IG development IS/OIDC bootstrap

The frontend navigates to `/v2/auth/is/login`; HTTPS Vite proxies `/v2` over
internal HTTP to FastAPI. The registered callback is
**https://5480-deb13.lan.com:5181/v2/auth/is/callback**. The post-login `/` redirect
returns to the same frontend origin. Flow/session cookies stay Secure and
HttpOnly. See [development HTTPS setup](local-development.md).

## Environment contract

`app/auth/is_oidc.py` reads these required variables:

| Variable | Meaning |
| --- | --- |
| `IS_OIDC_DISCOVERY_URL` | Discovery URL reachable from the API container |
| `IS_OIDC_CLIENT_ID` | Development confidential client ID supplied by IS |
| `IS_OIDC_CLIENT_SECRET` | Development client secret supplied by IS |
| `IS_OIDC_REDIRECT_URI` | Exact registered callback URL |

`IS_OIDC_POST_LOGIN_REDIRECT` is optional and defaults to `/`.
`IS_OIDC_ISSUER` is not consumed; the issuer is obtained from discovery. There
are no environment knobs for scopes, audience, PKCE, logout, state, nonce, or
cookie flags in the current implementation.

Keep credentials only in the existing ignored, owner-only `.env`. Do not reuse
cloud client credentials from `.env.local`; Docker development does not load that
file. Only the API needs OIDC configuration. The development worker uses an
explicit environment allowlist, excluding OIDC credentials; the frontend never
receives the client secret.

Scopes are fixed to `openid profile email`; the flow is authorization code with
PKCE S256. The verifier is generated with `token_urlsafe(64)`; state and nonce use
`token_urlsafe(32)`. Token exchange uses `client_secret_basic` and the PKCE verifier.
Discovery is cached per API process; recreate the API after environment changes.
The ID token is checked against discovery JWKS/signing algorithms (excluding
`none`), discovery issuer, client-ID audience, expiry, issued-at, subject, and
expected nonce. The access token is sent to userinfo, whose subject must match the
ID token; IG does not independently verify the access token as a JWT.

The transient cookies `ig_oidc_state`, `ig_oidc_nonce`, `ig_oidc_verifier`, and
`ig_oidc_flow` are host-only, HttpOnly, Secure, SameSite=Lax, path `/`, with a
10-minute lifetime. State comparison is constant-time. The `ig_session` cookie has
the same flags and a 30-day lifetime. Its random token is hashed in the local
session table. Browser refresh uses this local session, not an OAuth refresh
flow. Logout is local `POST /v2/auth/logout`: revoke the IG session and delete its
cookie. IG does not call IS end-session, and has no post-logout redirect setting.
Do not disable secure cookies for development. Non-localhost LAN HTTP origins
need HTTPS for these cookies; localhost browser exceptions are browser-dependent.

## Verified development discovery on 2026-11-07

The API container can retrieve:

`http://host.docker.internal:5180/api/auth/.well-known/openid-configuration`

The same document is served on the VM at:

`http://localhost:5180/api/auth/.well-known/openid-configuration`

It currently advertises:

- Issuer: `https://5480-deb13.lan.com:5180/api/auth`
- Authorization: `http://5480-deb13.lan.com:5180/api/auth/oauth2/authorize`
- Token: `http://5480-deb13.lan.com:5180/api/auth/oauth2/token`
- JWKS: `http://5480-deb13.lan.com:5180/api/auth/jwks`
- Userinfo: `http://5480-deb13.lan.com:5180/api/auth/oauth2/userinfo`
- End-session: `http://5480-deb13.lan.com:5180/api/auth/oauth2/end-session`
- Signing algorithm: EdDSA; PKCE S256; client_secret_basic supported.

The API development Compose configuration now maps `5480-deb13.lan.com` to the
configured persistent host gateway. Discovery and JWKS fetches succeed from the
API container. Host hosts-file entries are not inherited by containers; browsers
also need resolution of the exact advertised name (without an extra dot between
`deb` and `13`). Before acceptance, establish a browser-reachable development IS
origin. IS must
confirm that the advertised HTTPS issuer is intentional and exactly matches token
`iss`, or correct its development metadata. Do not override token validation to
hide a mismatch. No IS files or registrations were changed from IG.

## Required IS-side registration

Perform this in the dedicated IS project conversation:

| Setting | Required value |
| --- | --- |
| Application name | IG Development |
| Client type | Confidential |
| Grant / response type | authorization_code / code |
| Token authentication | client_secret_basic |
| PKCE | Required, S256 |
| Scopes | openid profile email |
| Redirect URI | https://5480-deb13.lan.com:5181/v2/auth/is/callback |
| Allowed origin, if IS requires it | https://5480-deb13.lan.com:5181 |
| Post-logout redirect URI | None required by IG; IG logout is local |

Return the development client ID and secret securely, and confirm the canonical
IS origin/issuer and hostname resolution. Do not invent a client ID or borrow the
cloud registration. The verified development discovery URL is configured in local `.env`. Supplied
client credentials have been loaded by recreating only the API; IG login now
returns HTTP 302 instead of a missing-configuration error. Development IS rejects
the supplied client with `invalid_client` / `client not found`. Register the client
in the actual development IS instance and supply its resulting credentials.

The registered HTTPS callback is preserved in `.env` and IG now serves trusted
HTTPS on that hostname/port. Its tenant-domain mapping resolves to the local
development tenant. Browser API calls retain the existing HTTP internal proxy;
secure-cookie and token validation are unchanged. The previous callback protocol
mismatch is resolved. The separate `invalid_client` registration issue remains.
Token exchange, authenticated session, and logout acceptance remain pending.

After IS supplies the real credentials and reachable metadata, update only local
`.env`, then recreate the API:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --no-build --no-deps --force-recreate ig
```

Validate the complete browser flow from the registered HTTPS origin: state/nonce/PKCE, callback,
token exchange/JWKS, subject mapping, local session, refresh, and local logout.
Tenant admission must also allow the intended identity: existing identities need
active local membership, and new identities need the tenant's JIT admission policy
or explicit provisioning. Never link users by matching email.

Missing required configuration currently raises KeyError. A small separate
improvement would validate the four required settings together and raise a
sanitized configuration error listing missing variable names. It is not implemented
as part of this bootstrap, and startup-wide enforcement needs a separate decision.
