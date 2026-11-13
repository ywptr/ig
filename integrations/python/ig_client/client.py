"""Internal synchronous HTTP client; async Job semantics remain explicit."""
from collections.abc import Mapping
import json
import math
import re
from threading import Event
import time
from typing import Any, cast
from urllib.parse import quote, urlsplit

from .auth import DelegatedContext, ServiceCredentials
from .contract import ACTIVE_STATES, TERMINAL_STATES
from .errors import (IGGenerationError, IGHTTPError, IGProtocolError, IGTransportError,
                     IGWaitCancelled, IGWaitTimeout)
from .models import (Artifact, Capabilities, EditorLaunchRequest, Execution, Job,
                     JobState, MediaResult, Submission)
from .transport import HTTPResponse, RequestsTransport, Transport


def _positive(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout/interval must be finite and positive")
    return value


def _id(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("A resource ID is required")
    return quote(value, safe="")


def _object(value) -> dict:
    if not isinstance(value, dict):
        raise IGProtocolError("Expected an IG JSON object")
    return value


def _string(value: dict, key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise IGProtocolError("Required IG response identity is missing")
    return result


def _state(value) -> JobState:
    if not isinstance(value, str) or value not in ACTIVE_STATES | TERMINAL_STATES:
        raise IGProtocolError("Unknown IG Job state; update the integration contract")
    return cast(JobState, value)


class IGClient:
    def __init__(self, base_url: str, credentials: ServiceCredentials, *,
                 default_timeout: float = 30, max_attempts: int = 3,
                 retry_interval: float = 0.5, transport: Transport | None = None):
        parsed = urlsplit(base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or parsed.scheme not in {"https", "http"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("IG base URL must be an HTTPS origin (loopback HTTP allowed for development)")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be 1–5")
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials
        self.default_timeout = _positive(default_timeout)
        self.max_attempts = max_attempts
        self.retry_interval = _positive(retry_interval)
        self.transport = transport if transport is not None else RequestsTransport()

    @staticmethod
    def _check_cancel(cancel: Event | None, job_id=None):
        if cancel is not None and cancel.is_set():
            raise IGWaitCancelled(job_id)

    def _request(self, method, path, context, scope, *, body=None, correlation_id=None,
                 timeout=None, cancel=None) -> HTTPResponse:
        budget = self.default_timeout if timeout is None else _positive(timeout)
        deadline = time.monotonic() + budget
        # Serialize once: every recovery attempt sends the exact same body/key.
        encoded = json.dumps(body, allow_nan=False, separators=(",", ":")).encode() if body is not None else None
        for attempt in range(self.max_attempts):
            self._check_cancel(cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IGTransportError("IG request budget exhausted; recover using the same identity")
            headers = {"Authorization": "Bearer " + self.credentials.token(context, [scope]),
                       "Accept": "application/json"}
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            if correlation_id is not None:
                headers["Idempotency-Key"] = correlation_id
            retry_after = self.retry_interval
            try:
                response = self.transport.request(method, self.base_url + path, headers=headers,
                    body=encoded, timeout=min(self.default_timeout, remaining))
                if 200 <= response.status < 300:
                    return response
                try:
                    detail = json.loads(response.body).get("detail")
                except (ValueError, AttributeError, UnicodeError):
                    detail = None
                error = IGHTTPError(response.status, detail)
                if not error.retryable:
                    raise error
                try:
                    retry_after = min(30, max(self.retry_interval, float(next(
                        (v for k, v in response.headers.items() if k.lower() == "retry-after"), "0"))))
                except (TypeError, ValueError):
                    pass
            except IGTransportError as exc:
                error = exc
            if attempt + 1 == self.max_attempts:
                raise error
            remaining = deadline - time.monotonic()
            if remaining <= retry_after:
                raise error
            if cancel is not None:
                if cancel.wait(retry_after):
                    raise IGWaitCancelled(None)
            else:
                time.sleep(retry_after)
        raise AssertionError("Unreachable")

    def _json(self, method, path, context, scope, **kwargs):
        response = self._request(method, path, context, scope, **kwargs)
        try:
            return json.loads(response.body)
        except (ValueError, UnicodeError):
            raise IGProtocolError("IG returned invalid JSON") from None

    def discover_capabilities(self, context: DelegatedContext, *, timeout=None) -> Capabilities:
        data = _object(self._json("GET", "/v2/media/capabilities", context, "media:discover", timeout=timeout))
        if data.get("schema_version") != 1 or not isinstance(data.get("media_types"), list) or not isinstance(data.get("interface_levels"), dict):
            raise IGProtocolError("Unsupported IG capability contract")
        return cast(Capabilities, data)

    def submit_generation(self, context: DelegatedContext, *, correlation_id: str,
                          request: Mapping[str, Any], timeout=None, cancel: Event | None = None) -> Submission:
        if not isinstance(correlation_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", correlation_id):
            raise ValueError("Correlation ID must satisfy the IG Idempotency-Key contract")
        if not isinstance(request, Mapping):
            raise ValueError("Generation request must be a mapping")
        body = dict(request)
        if set(body) & {"user_id", "tenant_id", "actor", "act", "email"}:
            raise ValueError("Identity belongs exclusively in DelegatedContext")
        for reference in body.get("references", []):
            if (not isinstance(reference, Mapping) or not reference.get("artifact_id")
                or set(reference) - {"artifact_id", "role", "strength"}):
                raise ValueError("References must use Artifact IDs, roles and optional strength")
        data = _object(self._json("POST", "/v2/media/generations", context, "media:generate",
            body=body, correlation_id=correlation_id, timeout=timeout, cancel=cancel))
        return Submission(correlation_id, _string(data, "job_id"), _string(data, "request_id"),
                          _string(data, "media_type"), _state(data.get("status")))

    def get_job(self, context: DelegatedContext, job_id: str, *, timeout=None, cancel=None) -> Job:
        data = _object(self._json("GET", f"/v2/jobs/{_id(job_id)}", context, "jobs:read", timeout=timeout, cancel=cancel))
        return Job(_string(data, "job_id"), _state(data.get("status")), data.get("error_code"), data.get("error_message"))

    @staticmethod
    def _execution(data, job_id=None):
        data = _object(data)
        metadata = _object(data.get("metadata") or {})
        if type(data.get("attempt")) is not int or data["attempt"] < 1:
            raise IGProtocolError("Invalid IG Execution attempt")
        # The current server emits artifact_id. Multiple completed Executions
        # and the additive artifact_ids shape are both representable.
        identifiers = metadata.get("artifact_ids", [])
        if not isinstance(identifiers, list) or any(not isinstance(item, str) or not item for item in identifiers):
            raise IGProtocolError("Invalid IG artifact result identities")
        identifiers = list(identifiers)
        if metadata.get("artifact_id"):
            identifiers.insert(0, _string(metadata, "artifact_id"))
        return Execution(_string(data, "execution_id"), job_id or _string(data, "job_id"),
            _state(data.get("status")), data["attempt"], tuple(dict.fromkeys(identifiers)),
            data.get("error_code"), data.get("provider"), data.get("model"))

    def get_job_executions(self, context: DelegatedContext, job_id: str, *, timeout=None) -> tuple[Execution, ...]:
        rows = self._json("GET", f"/v2/jobs/{_id(job_id)}/executions", context, "jobs:read", timeout=timeout)
        if not isinstance(rows, list):
            raise IGProtocolError("Expected an IG Execution list")
        return tuple(self._execution(row, job_id) for row in rows)

    def get_execution(self, context: DelegatedContext, execution_id: str, *, timeout=None) -> Execution:
        return self._execution(self._json("GET", f"/v2/executions/{_id(execution_id)}", context, "jobs:read", timeout=timeout))

    def get_artifact(self, context: DelegatedContext, artifact_id: str, *, timeout=None) -> Artifact:
        data = _object(self._json("GET", f"/v2/artifacts/{_id(artifact_id)}", context, "artifacts:read", timeout=timeout))
        generation = data.get("generation") or {}
        # Explicit projection: never return arbitrary metadata/storage locators.
        preview = data.get("preview")
        if isinstance(preview, dict):
            preview = {key: preview[key] for key in ("status", "kind", "version", "mime_type", "width", "height",
                "size_bytes", "frame_count", "positions", "error_code") if key in preview}
        else:
            preview = None
        properties = data.get("media_properties") or {}
        return Artifact(_string(data, "artifact_id"), _string(data, "media_type"), data.get("mime_type"),
            data.get("filename"), data.get("size_bytes"), generation.get("job_id"), generation.get("execution_id"),
            preview, {key: properties[key] for key in ("width", "height", "duration_seconds", "fps") if key in properties},
            data.get("content_available", True))

    def get_artifact_content(self, context: DelegatedContext, artifact_id: str, *, timeout=None) -> bytes:
        return self._request("GET", f"/v2/artifacts/{_id(artifact_id)}/content", context, "artifacts:read", timeout=timeout).body

    def get_artifact_preview(self, context: DelegatedContext, artifact_id: str, *, version: int = 1, timeout=None) -> HTTPResponse:
        if type(version) is not int or version < 1:
            raise ValueError("Preview version must be a positive integer")
        # 200 is WebP; 202 is processing JSON. Preserve that distinction.
        response = self._request("GET", f"/v2/artifacts/{_id(artifact_id)}/preview?v={version}", context,
                                 "artifacts:read", timeout=timeout)
        safe_headers = {key: value for key, value in response.headers.items()
                        if key.lower() in {"content-type", "retry-after", "etag"}}
        return HTTPResponse(response.status, safe_headers, response.body)

    def wait_for_job(self, context: DelegatedContext, job_id: str, *, timeout: float,
                     poll_interval: float = 3, cancel: Event | None = None) -> Job:
        deadline = time.monotonic() + _positive(timeout)
        _positive(poll_interval)
        last = None
        while True:
            self._check_cancel(cancel, job_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IGWaitTimeout(job_id, last)
            try:
                last = self.get_job(context, job_id, timeout=remaining, cancel=cancel)
            except IGWaitCancelled:
                raise IGWaitCancelled(job_id) from None
            except (IGHTTPError, IGTransportError) as exc:
                if not exc.retryable:
                    raise
            else:
                if last.status in TERMINAL_STATES:
                    return last
            delay = min(poll_interval, max(0, deadline - time.monotonic()))
            if cancel is not None:
                if cancel.wait(delay):
                    raise IGWaitCancelled(job_id)
            else:
                time.sleep(delay)

    def generate_and_wait(self, context: DelegatedContext, *, correlation_id: str,
                          request: Mapping[str, Any], timeout: float,
                          poll_interval: float = 3, cancel: Event | None = None) -> MediaResult:
        deadline = time.monotonic() + _positive(timeout)
        def remaining(job_id=None):
            self._check_cancel(cancel, job_id)
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise IGWaitTimeout(job_id)
            return budget
        submission = self.submit_generation(context, correlation_id=correlation_id, request=request,
                                            timeout=remaining(), cancel=cancel)
        job = self.wait_for_job(context, submission.job_id, timeout=remaining(submission.job_id),
                                poll_interval=poll_interval, cancel=cancel)
        if job.status != "completed":
            raise IGGenerationError(MediaResult(submission, job, (), ()))
        executions = self.get_job_executions(context, job.job_id, timeout=remaining(job.job_id))
        artifacts, seen = [], set()
        for execution in executions:
            if execution.status != "completed":
                continue
            for artifact_id in execution.artifact_ids:
                if artifact_id not in seen:
                    artifact = self.get_artifact(context, artifact_id, timeout=remaining(job.job_id))
                    artifacts.append(artifact)
                    seen.add(artifact_id)
        return MediaResult(submission, job, executions, tuple(artifacts))

    def create_editor_session(self, context: DelegatedContext, request: EditorLaunchRequest):
        """Reserved IG#0010 boundary. No JWT, HTTP call or session is created."""
        raise NotImplementedError("Embedded editor sessions are reserved for IG#0010")
