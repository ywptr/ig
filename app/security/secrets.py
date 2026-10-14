import json
import os

from cryptography.fernet import Fernet


def get_secret_cipher() -> Fernet:
    key = os.environ[
        "TENANT_SECRET_ENCRYPTION_KEY"
    ]

    return Fernet(
        key.encode("utf-8")
    )


def encrypt_secret(
    value: str,
) -> str:
    cipher = get_secret_cipher()

    return cipher.encrypt(
        value.encode("utf-8")
    ).decode("utf-8")


def decrypt_secret(
    value: str,
) -> str:
    cipher = get_secret_cipher()

    return cipher.decrypt(
        value.encode("utf-8")
    ).decode("utf-8")


def encrypt_credentials(
    credentials: dict[str, str],
) -> str:
    serialized = json.dumps(
        credentials,
        separators=(",", ":"),
        sort_keys=True,
    )

    return encrypt_secret(
        serialized
    )


def decrypt_credentials(
    encrypted_value: str,
) -> dict[str, str]:
    serialized = decrypt_secret(
        encrypted_value
    )

    credentials = json.loads(
        serialized
    )

    if not isinstance(credentials, dict):
        raise RuntimeError(
            "Invalid provider credentials payload"
        )

    return {
        str(key): str(value)
        for key, value in credentials.items()
    }