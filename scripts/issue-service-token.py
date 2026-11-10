"""Development/offline signing helper. Writes the bearer token only to a new 0600 file."""
import argparse
import os
from pathlib import Path
import sys
import time

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.auth.service_contract import AUDIENCE, MAX_TOKEN_SECONDS, SCOPES, TOKEN_TYPE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("service-id", "key-id", "actor-issuer", "actor-subject", "tenant-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--private-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--ttl", type=int, default=MAX_TOKEN_SECONDS)
    args = parser.parse_args()
    if not 0 < args.ttl <= MAX_TOKEN_SECONDS or not set(args.scope) <= SCOPES:
        parser.error("Invalid lifetime or scopes")
    try:
        now = int(time.time())
        token = jwt.encode({"iss": args.service_id, "sub": args.service_id, "aud": AUDIENCE,
            "iat": now, "exp": now + args.ttl, "scope": " ".join(sorted(set(args.scope))),
            "act": {"iss": args.actor_issuer, "sub": args.actor_subject}, "tenant_id": args.tenant_id},
            args.private_key_file.read_bytes(), algorithm="RS256", headers={"kid": args.key_id, "typ": TOKEN_TYPE})
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(token)
    except Exception:
        parser.exit(2, "Token issuance failed; check key and output file (must not already exist).\n")


if __name__ == "__main__":
    main()
