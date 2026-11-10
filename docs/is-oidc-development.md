# IG development IS/OIDC bootstrap

The frontend navigates to `/v2/auth/is/login`; HTTPS Vite proxies `/v2` over
internal HTTP to FastAPI. The registered callback is
**https://5480-deb13.lan.com:5181/v2/auth/is/callback**. The post-login `/` redirect
returns to the same frontend origin. Flow/session cookies stay Secure and
HttpOnly. See [development HTTPS setup](local-development.md).

Before the first login, run [tenant bootstrap](tenant-bootstrap.md) to establish
domains/settings and explicit JIT admission. After login, use its issuer/subject
promotion command for the first administrator. Provider setup remains separate.

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

## Verified identity provider on 2026-11-09

IG development intentionally authenticates against the production IS identity
service. Its application data remains local PostgreSQL `ig_dev`; queue execution
remains host Redis/RQ and artifacts remain local filesystem. Selecting a production
identity provider does not authorize use of production IG database credentials.

Discovery:

`https://sandcastle-auth.vercel.app/api/auth/.well-known/openid-configuration`

Verified advertised endpoints:

- Issuer: `https://sandcastle-auth.vercel.app/api/auth`
- Authorization: `https://sandcastle-auth.vercel.app/api/auth/oauth2/authorize`
- Token: `https://sandcastle-auth.vercel.app/api/auth/oauth2/token`
- JWKS: `https://sandcastle-auth.vercel.app/api/auth/jwks`
- Userinfo: `https://sandcastle-auth.vercel.app/api/auth/oauth2/userinfo`

Discovery and JWKS fetches pass from the API container. The registered client now
reaches IS `/sign-in`, rather than `invalid_client`. Local `.env` uses HTTPS
directly for discovery; no initial HTTP redirect is required.

The old development IS listener on port 5180 is not used by the current flow. IG
browser clients still need DNS/hosts-file resolution for `5480-deb13.lan.com` to
`192.168.0.99`, and must trust the existing development CA. Host hosts-file entries
are not automatically inherited by containers.

## Required IS-side registration

Manage the client in the dedicated IS conversation on the configured identity
provider. Its required IG development contract is:

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

Client credentials remain only in ignored `.env` and the API environment; they
are not passed to the frontend or worker. The registered HTTPS callback is
preserved and matches the trusted Vite origin. Its tenant-domain mapping resolves
to the local development tenant. Secure-cookie and token validation remain
unchanged; no IS registration or account was changed from this repository.

Verification performed on 2026-11-09:

- Browser clicked `Continue with IS` and reached production IS sign-in over HTTPS.
- Authorization code flow, exact registered callback, requested scopes and S256
  challenge were checked without displaying client credentials or flow values.
- Browser state/nonce matched their Secure/HttpOnly/SameSite=Lax cookies.
- Read-only local checks found a production-IS identity, active tenant membership,
  and active local session. JIT admission is enabled for this development tenant.
- A fresh authenticated callback, session refresh and logout acceptance require
  the developer's interactive account; the automated browser did not submit human
  credentials or reuse another browser's cookies.

After changing the client/provider settings, update only local `.env`, then
recreate the API:

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
