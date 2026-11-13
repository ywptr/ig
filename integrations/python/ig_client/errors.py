"""Safe exception messages; original IG detail is available explicitly."""
from typing import Any


class IGError(Exception):
    pass


class IGHTTPError(IGError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        self.code = detail.get("code") if isinstance(detail, dict) else None
        self.retryable = status in {408, 429, 500, 502, 503, 504}
        self.category = ("authorization" if status in {401, 403} else
                         "idempotency_conflict" if self.code == "IDEMPOTENCY_CONFLICT" else
                         "not_found" if status == 404 else
                         "transient" if self.retryable else "request")
        # Neither detail text nor response bodies enter exception/log messages.
        super().__init__(f"IG HTTP {status} ({self.category})")


class IGTransportError(IGError):
    retryable = True


class IGProtocolError(IGError):
    retryable = False


class IGWaitTimeout(IGError):
    def __init__(self, job_id: str | None, last_job=None):
        self.job_id, self.last_job = job_id, last_job
        super().__init__("IG client wait deadline exceeded; resume the same Job/submission")


class IGWaitCancelled(IGError):
    def __init__(self, job_id: str | None):
        self.job_id = job_id
        super().__init__("IG client wait canceled; remote work was not canceled")


class IGGenerationError(IGError):
    def __init__(self, result):
        self.result, self.job = result, result.job
        self.code = result.job.error_code
        self.retryable = False
        self.category = ("provider_uncertainty" if self.code == "VIDEO_SUBMISSION_UNCERTAIN" else
                         "generation_timeout" if self.code == "VIDEO_PENDING_TIMEOUT" else
                         "canceled" if result.job.status == "canceled" else "generation_failure")
        super().__init__(f"IG generation ended: {self.category}")
