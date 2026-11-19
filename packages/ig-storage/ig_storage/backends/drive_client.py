"""Google Drive v3 transport bound to explicit runtime credentials."""
import hashlib
import re
import time
from urllib.parse import urlparse

import requests

from ..locator import is_drive_id

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
CHUNK_BYTES = 8 * 1024 * 1024  # Multiple of Google's 256 KiB chunk quantum.


class DriveError(RuntimeError):
    def __init__(self, code="unknown_error"):
        self.code = code
        super().__init__(f"Google Drive storage unavailable ({code})")


def valid_id(value):
    if not is_drive_id(value):
        raise DriveError("invalid_locator")
    return value


class DriveClient:
    def __init__(self, access_token: str, *, timeout: float = 60):
        if not access_token:
            raise DriveError("credential_unavailable")
        self._access_token = access_token
        self.timeout = timeout

    def request(self, method, url, *, allowed=(200,), **kwargs):
        try:
            response = requests.request(method, url, headers={"Authorization": "Bearer " + self._access_token,
                **kwargs.pop("headers", {})}, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise DriveError() from None
        if response.status_code not in allowed:
            code = {401: "revoked", 403: "permission_lost", 404: "not_found"}.get(response.status_code, "unknown_error")
            response.close()
            raise DriveError(code)
        return response

    def json(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise DriveError()
            return result
        except (ValueError, TypeError):
            raise DriveError() from None
        finally:
            response.close()

    def stat(self, file_id):
        return self.json("GET", API + "/files/" + valid_id(file_id),
            params={"fields": "id,mimeType,trashed,size,md5Checksum,capabilities(canAddChildren)"})

    def check_folder(self, folder_id):
        try:
            folder = self.stat(folder_id)
            if (folder.get("trashed") or folder.get("mimeType") != FOLDER
                or not folder.get("capabilities", {}).get("canAddChildren")):
                raise DriveError("folder_unavailable")
        except DriveError as exc:
            if exc.code == "not_found":
                raise DriveError("folder_unavailable") from None
            raise

    def allocate_id(self):
        return valid_id(self.json("GET", API + "/files/generateIds", params={"count": 1, "space": "drive"})["ids"][0])

    def upload(self, folder_id, filename, data, *, app_properties=None):
        """Resumable chunks; one preallocated file ID across all bounded retries.

        The existing media pipeline supplies bytes. memoryview avoids a second
        whole-output copy; only individual HTTP chunks are materialized.
        """
        self.check_folder(folder_id)
        identity = self.allocate_id()
        total = len(data)
        if not total:
            raise DriveError("empty_content")
        metadata = {"id": identity, "name": filename, "parents": [folder_id]}
        if app_properties is not None:
            metadata["appProperties"] = dict(app_properties)
        response = self.request("POST", UPLOAD, params={"uploadType": "resumable", "fields": "id"},
            json=metadata, headers={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(total)})
        location = response.headers.get("Location", "")
        response.close()
        parsed = urlparse(location)
        if parsed.scheme != "https" or parsed.netloc != "www.googleapis.com" or parsed.path != "/upload/drive/v3/files":
            raise DriveError("invalid_upload_session")
        offset, failures = 0, 0
        while offset < total and failures < 3:
            end = min(offset + CHUNK_BYTES, total)
            try:
                response = self.request("PUT", location, allowed=(200, 201, 308),
                    headers={"Content-Range": f"bytes {offset}-{end - 1}/{total}"}, data=memoryview(data)[offset:end])
            except DriveError as exc:
                if exc.code != "unknown_error":
                    raise
                failures += 1
                time.sleep(0.25 * 2 ** (failures - 1))
                # Probe the same session after an uncertain chunk acknowledgement.
                try:
                    response = self.request("PUT", location, allowed=(200, 201, 308),
                        headers={"Content-Range": f"bytes */{total}", "Content-Length": "0"}, data=b"")
                except DriveError:
                    continue
            try:
                if response.status_code in (200, 201):
                    if response.json().get("id") != identity:
                        raise DriveError("invalid_upload_result")
                    return identity
                acknowledged = response.headers.get("Range", "")
                match = re.fullmatch(r"bytes=0-(\d+)", acknowledged)
                next_offset = int(match[1]) + 1 if match else 0
                if next_offset < offset or next_offset > total:
                    raise DriveError("invalid_upload_result")
                if next_offset == offset:
                    failures += 1
                offset = next_offset
            finally:
                response.close()
        # A lost final response may still have completed the file. Verify bytes.
        try:
            stored = self.stat(identity)
            if int(stored.get("size", -1)) == total and stored.get("md5Checksum") == hashlib.md5(data, usedforsecurity=False).hexdigest():
                return identity
        except (DriveError, ValueError, TypeError):
            pass
        raise DriveError("upload_failed")

    def iter_bytes(self, file_id, chunk_size):
        response = self.request("GET", API + "/files/" + valid_id(file_id), params={"alt": "media"}, stream=True)
        try:
            yield from response.iter_content(chunk_size=chunk_size)
        except requests.RequestException:
            raise DriveError() from None
        finally:
            response.close()

    def delete(self, file_id):
        self.request("DELETE", API + "/files/" + valid_id(file_id), allowed=(204, 404)).close()
