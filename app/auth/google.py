import os
from dataclasses import dataclass

from google.auth.transport import requests
from google.oauth2 import id_token


GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]


@dataclass
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture_url: str | None


def verify_google_id_token(
    token: str,
) -> GoogleIdentity:
    payload = id_token.verify_oauth2_token(
        token,
        requests.Request(),
        GOOGLE_CLIENT_ID,
    )

    return GoogleIdentity(
        sub=payload["sub"],
        email=payload["email"],
        name=payload.get("name"),
        picture_url=payload.get("picture"),
    )