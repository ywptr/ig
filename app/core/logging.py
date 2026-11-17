import logging
import os


def configure_logging() -> None:
    level_name = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    level = getattr(
        logging,
        level_name,
        logging.INFO,
    )

    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s "
            "%(message)s"
        ),
    )

    for handler in logging.getLogger().handlers + logging.getLogger("uvicorn.access").handlers:
        if not any(isinstance(item, DriveOAuthLogFilter) for item in handler.filters):
            handler.addFilter(DriveOAuthLogFilter())

    for name in ("uvicorn.access", "httpx", "httpx2"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, DriveOAuthLogFilter) for item in logger.filters):
            logger.addFilter(DriveOAuthLogFilter())


def job_trace_enabled() -> bool:
    return (
        os.getenv(
            "IG_TRACE_JOBS",
            "false",
        ).lower()
        in {"1", "true", "yes", "on"}
    )

class DriveOAuthLogFilter(logging.Filter):
    """Authorization codes/state must not enter application or access logs."""
    def filter(self, record):
        import re
        def redact(value):
            rendered = str(value)
            if "/v2/user/storage/google-drive/callback?" not in rendered:
                return value
            return re.sub(r"(/v2/user/storage/google-drive/callback)\?[^\s\"']*", r"\1?[redacted]", rendered)
        # Preserve positional arguments: Uvicorn's AccessFormatter unpacks them.
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        return True
