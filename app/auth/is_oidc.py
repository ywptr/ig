import base64
import hashlib
import os
from functools import lru_cache
from secrets import token_urlsafe
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from jwt import PyJWKClient


DISCOVERY_URL_ENV = (
    "IS_OIDC_DISCOVERY_URL"
)


@lru_cache(maxsize=1)
def get_oidc_discovery() -> dict[str, Any]:
    discovery_url = os.environ[
        DISCOVERY_URL_ENV
    ]

    response = requests.get(
        discovery_url,
        timeout=10,
    )
    response.raise_for_status()

    discovery = response.json()

    required = {
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "jwks_uri",
    }

    missing = required.difference(
        discovery
    )

    if missing:
        raise RuntimeError(
            "OIDC discovery is missing: "
            + ", ".join(
                sorted(missing)
            )
        )

    return discovery


def get_client_id() -> str:
    return os.environ[
        "IS_OIDC_CLIENT_ID"
    ]


def get_client_secret() -> str:
    return os.environ[
        "IS_OIDC_CLIENT_SECRET"
    ]


def get_redirect_uri() -> str:
    return os.environ[
        "IS_OIDC_REDIRECT_URI"
    ]


def get_post_login_redirect() -> str:
    return os.getenv(
        "IS_OIDC_POST_LOGIN_REDIRECT",
        "/",
    )


def create_pkce_verifier() -> str:
    return token_urlsafe(64)


def create_pkce_challenge(
    verifier: str,
) -> str:
    digest = hashlib.sha256(
        verifier.encode("ascii")
    ).digest()

    return (
        base64.urlsafe_b64encode(
            digest
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def create_authorization_request() -> tuple[
    str,
    str,
    str,
    str,
]:
    discovery = get_oidc_discovery()

    state = token_urlsafe(32)
    nonce = token_urlsafe(32)
    verifier = create_pkce_verifier()

    challenge = create_pkce_challenge(
        verifier
    )

    parameters = {
        "response_type": "code",
        "client_id": get_client_id(),
        "redirect_uri": (
            get_redirect_uri()
        ),
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    authorization_url = (
        discovery[
            "authorization_endpoint"
        ]
        + "?"
        + urlencode(parameters)
    )

    return (
        authorization_url,
        state,
        nonce,
        verifier,
    )


def exchange_authorization_code(
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    discovery = get_oidc_discovery()

    response = requests.post(
        discovery["token_endpoint"],
        auth=(
            get_client_id(),
            get_client_secret(),
        ),
        data={
            "grant_type": (
                "authorization_code"
            ),
            "code": code,
            "redirect_uri": (
                get_redirect_uri()
            ),
            "code_verifier": (
                code_verifier
            ),
        },
        timeout=15,
    )

    response.raise_for_status()

    token_response = response.json()

    if "id_token" not in token_response:
        raise RuntimeError(
            "IS token response did not "
            "contain an id_token"
        )

    return token_response

def get_userinfo(
    *,
    access_token: str,
) -> dict[str, Any]:
    discovery = get_oidc_discovery()

    userinfo_endpoint = discovery.get(
        "userinfo_endpoint"
    )

    if not userinfo_endpoint:
        raise RuntimeError(
            "OIDC discovery does not expose a userinfo_endpoint"
        )

    response = requests.get(
        userinfo_endpoint,
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            ),
        },
        timeout=10,
    )

    response.raise_for_status()

    userinfo = response.json()

    if "sub" not in userinfo:
        raise RuntimeError(
            "IS userinfo response does not contain sub"
        )

    return userinfo


def verify_id_token(
    *,
    id_token: str,
    expected_nonce: str,
) -> dict[str, Any]:
    discovery = get_oidc_discovery()

    jwks_client = PyJWKClient(
        discovery["jwks_uri"]
    )

    signing_key = (
        jwks_client.get_signing_key_from_jwt(
            id_token
        )
    )

    algorithms = discovery.get(
        "id_token_signing_alg_values_supported"
    ) or ["RS256"]

    algorithms = [
        algorithm
        for algorithm in algorithms
        if algorithm.lower() != "none"
    ]

    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=algorithms,
        audience=get_client_id(),
        issuer=discovery["issuer"],
        options={
            "require": [
                "exp",
                "iat",
                "iss",
                "sub",
            ],
        },
    )

    if (
        claims.get("nonce")
        != expected_nonce
    ):
        raise ValueError(
            "OIDC nonce mismatch"
        )

    return claims