"""Version-one deterministic preview policy; no application/database dependencies."""
import math

VERSION = 1
IMAGE_EDGE = 512
FRAME_SIZE = (256, 144)
MAX_PIXELS = 20_000_000
MAX_DIMENSION = 8192
MAX_VIDEO_SECONDS = 600
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
PROCESS_TIMEOUT = 25


def frame_positions(duration: float) -> list[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid duration")
    count = 1 if duration < 5 else 3 if duration <= 15 else 5 if duration <= 30 else 6
    return [round(duration * (0.5 if count == 1 else 0.1 + 0.8 * i / (count - 1)), 6)
            for i in range(count)]


def kind_for(media_type: str, mime_type: str | None) -> str | None:
    if media_type == "image" and mime_type in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return "thumbnail"
    if media_type == "video" and mime_type == "video/mp4":
        return "contact_sheet"
    return None
