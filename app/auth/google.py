
from dataclasses import dataclass

from google.auth.transport import requests
from google.oauth2 import id_token


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture_url: str | None


def verify_google_id_token(
    token: str,
    *,
    client_id: str,
) -> GoogleIdentity:

    payload = id_token.verify_oauth2_token(
        token,
        requests.Request(),
        client_id,
    )

    if not payload.get("email_verified"):
        raise ValueError(
            "Google email is not verified"
        )

    return GoogleIdentity(
        sub=payload["sub"],
        email=payload["email"].strip().lower(),
        name=payload.get("name"),
        picture_url=payload.get("picture"),
    )