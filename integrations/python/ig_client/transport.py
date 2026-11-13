"""Bounded HTTP transport. No sessions, cookies, redirects or URL-bearing errors."""
from dataclasses import dataclass, field
import time
from typing import Mapping, Protocol

import requests

from .errors import IGProtocolError, IGTransportError


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str],
                body: bytes | None, timeout: float) -> HTTPResponse: ...


class RequestsTransport:
    def __init__(self, max_response_bytes: int = 128 * 1024 * 1024):
        if max_response_bytes <= 0:
            raise ValueError("Response byte limit must be positive")
        self.max_response_bytes = max_response_bytes

    def request(self, method, url, *, headers, body, timeout):
        deadline = time.monotonic() + timeout
        try:
            # A fresh session avoids cookies/netrc credentials crossing contexts.
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, url, headers=headers, data=body, timeout=timeout,
                                     stream=True, allow_redirects=False) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise IGProtocolError("IG response exceeded configured byte limit")
                        if time.monotonic() >= deadline:
                            raise IGTransportError("IG response read timed out")
                        chunks.append(chunk)
                    return HTTPResponse(response.status_code, dict(response.headers), b"".join(chunks))
        except requests.RequestException:
            raise IGTransportError("IG HTTP transport failed; retry the same operation identity") from None
