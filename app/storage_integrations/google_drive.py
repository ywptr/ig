"""IG Drive OAuth and account setup."""
import os
from urllib.parse import urlparse

import requests

from ig_storage.backends.drive_client import API, UPLOAD, FOLDER, DriveClient as StorageDriveClient, DriveError, valid_id

SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN = "https://oauth2.googleapis.com/token"


def configuration():
    values = {key: os.getenv("GOOGLE_DRIVE_" + key, "") for key in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI")}
    if not all(values.values()):
        raise DriveError("not_configured")
    uri = urlparse(values["REDIRECT_URI"])
    if (uri.scheme != "https" and not (uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"})) or uri.fragment or uri.username:
        raise DriveError("not_configured")
    return values


def token_request(data):
    config = configuration()
    try:
        response = requests.post(TOKEN, data={"client_id": config["CLIENT_ID"],
            "client_secret": config["CLIENT_SECRET"], **data}, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            response.close()
            raise DriveError("revoked" if response.status_code in (400, 401) else "unknown_error")
        try:
            result = response.json()
        finally:
            response.close()
        if not isinstance(result, dict):
            raise DriveError()
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise DriveError()
        if "scope" in result and (not isinstance(result["scope"], str) or SCOPE not in result["scope"].split()):
            raise DriveError("permission_lost")
        return result
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise DriveError() from None


class DriveClient(StorageDriveClient):
    """Apply IG's own OAuth account and upload metadata conventions."""

    def account(self):
        user = self.json("GET", API + "/about", params={"fields": "user(permissionId,emailAddress,displayName)"}).get("user")
        if not isinstance(user, dict) or not isinstance(user.get("permissionId"), str) or not user["permissionId"]:
            raise DriveError("permission_lost")
        return user

    def create_folder(self):
        identity = self.allocate_id()
        self.json("POST", API + "/files", json={"id": identity, "name": "IG Artifacts", "mimeType": FOLDER,
            "appProperties": {"ig_schema": "1", "ig_kind": "folder"}}, params={"fields": "id"})
        return identity

    def upload(self, folder_id, filename, data):
        properties = {"ig_schema": "1", "ig_kind": "preview" if filename == "preview.webp" else "artifact"}
        return super().upload(folder_id, filename, data, app_properties=properties)
