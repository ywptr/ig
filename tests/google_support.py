"""Synthetic Google HTTP fixtures; no credentials or external calls."""
import base64
import json

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6kUAAAAAASUVORK5CYII=")
JPEG = b"\xff\xd8\xff" + b"synthetic-jpeg"
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"synthetic-webp"
TEST_CREDENTIAL = "test-only-not-a-google-key"
MODEL = "gemini-3.1-flash-lite-image"


def response_body(*, data=PNG, mime_type="image/png"):
    return {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "Provider prose that must not be persisted"},
            {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(data).decode("ascii")}},
        ]}}],
        "responseId": "test-response-1", "usageMetadata": {
            "promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30,
            "private": "not-for-persistence",
        }, "raw_secret": TEST_CREDENTIAL,
    }


class GoogleResponse:
    def __init__(self, body=None, *, status=200, raw=None):
        self.status_code = status
        self.data = raw if raw is not None else json.dumps(body if body is not None else response_body()).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]
