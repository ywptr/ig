#!/usr/bin/env python3
"""Operator-only smoke test after connecting a dedicated development Drive account.

Requires an explicit development URL and IG_DRIVE_DEV_SESSION in the shell.
Never discovers credentials, connects OAuth, or invokes a generation provider.
"""
import argparse
from io import BytesIO
import os
import sys
import time
from urllib.parse import urlparse
import requests
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Explicit dedicated development IG origin")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        parser.error("Use HTTPS or localhost HTTP")
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error("Supply an origin without credentials, path or query")
    cookie = os.getenv("IG_DRIVE_DEV_SESSION")
    if not cookie:
        parser.error("Set IG_DRIVE_DEV_SESSION privately to the development user's IG session cookie")
    session = requests.Session()
    session.cookies.set("ig_session", cookie)
    session.headers["Origin"] = base
    def call(method, path, **kwargs):
        response = session.request(method, base + path, timeout=60, allow_redirects=False, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(f"Development IG request failed (HTTP {response.status_code})")
        return response
    current = call("GET", "/v2/user/storage").json()
    if not current.get("google_drive_enabled") or (current.get("connection") or {}).get("status") != "healthy":
        raise RuntimeError("Enable Drive and connect a healthy dedicated development account in My storage first")
    changed = False
    try:
        call("PATCH", "/v2/user/storage", json={"artifact_storage_preference": "google_drive"})
        changed = True
        image = BytesIO()
        Image.new("RGB", (64, 48), (30, 90, 150)).save(image, "PNG")
        data = image.getvalue()
        uploaded = call("POST", "/v2/artifacts/uploads", files={"file": ("ig-drive-development.png", data, "image/png")},
                        data={"origin": "reference"}).json()
        identity = uploaded["artifact_id"]
        assert "storage_uri" not in uploaded
        assert call("GET", f"/v2/artifacts/{identity}/content").content == data
        for attempt in range(10):
            preview = call("GET", f"/v2/artifacts/{identity}/preview")
            if preview.status_code == 200 and preview.headers.get("Content-Type", "").startswith("image/webp"):
                break
            if preview.status_code != 202:
                raise RuntimeError("Development preview did not become available")
            time.sleep(1)
        else:
            raise RuntimeError("Development preview remained pending")
        print(f"Development upload/content/preview OK. Artifact ID: {identity}")
        print("Verify the original and preview files in the dedicated account's IG Artifacts folder.")
    finally:
        if changed:
            call("PATCH", "/v2/user/storage", json={"artifact_storage_preference": "ig_managed"})
            print("Storage preference switched to IG managed; uploaded test files were retained.")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError, AssertionError, KeyError, ValueError):
        print("Drive development smoke test failed. Check the user's connection and development server; no credentials are printed.", file=sys.stderr)
        sys.exit(1)
