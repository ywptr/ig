"""Bounded binary MP4 download. Each adapter authorizes its own CDN URL."""
from time import monotonic as monotonic_clock

import requests

from ig_core.providers.video import VideoProviderError, VideoProviderTerminalError


def download_mp4(url: str, *, max_bytes: int, deadline_seconds: int,
                 timeout: tuple[int, int], monotonic=monotonic_clock) -> bytes:
    deadline = monotonic() + deadline_seconds
    try:
        # No API authorization on the storage request and no redirects.
        with requests.get(url, stream=True, timeout=timeout, allow_redirects=False) as response:
            if response.status_code != 200:
                raise VideoProviderError("Video output download failed")
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            if content_type not in {"video/mp4", "application/octet-stream"}:
                raise VideoProviderTerminalError("Video output has an unsupported content type")
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > max_bytes):
                raise VideoProviderTerminalError("Video output exceeds the download limit")
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if monotonic() > deadline:
                    raise VideoProviderError("Video download deadline exceeded")
                total += len(chunk)
                if total > max_bytes:
                    raise VideoProviderTerminalError("Video output exceeds the download limit")
                chunks.append(chunk)
            data = b"".join(chunks)
    except requests.RequestException:
        raise VideoProviderError("Video output download unavailable") from None
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise VideoProviderTerminalError("Video output is not an MP4 file")
    return data
