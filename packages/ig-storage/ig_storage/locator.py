import re
from urllib.parse import ParseResult, urlparse


def parse_locator(locator: str) -> ParseResult:
    try:
        if not isinstance(locator, str) or any(ord(char) < 32 for char in locator):
            raise ValueError
        parsed = urlparse(locator)
        if not parsed.scheme:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise ValueError("Invalid storage locator") from None


def is_drive_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is not None
