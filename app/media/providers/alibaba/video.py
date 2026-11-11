"""Alibaba Model Studio Wan API translation. No job/control-plane dependency."""
from app.media.technical import TechnicalCapabilities, ExecutionBehavior, ReferenceInputSlot

import base64
import re
import time
from urllib.parse import urlparse, quote

import requests
from app.media.capability_schemas import GenerationControls, IntegerRange, ModelMetadata, ProviderMetadata
from app.media.constraints import (
    VIDEO_ASPECT_RATIOS, VIDEO_RESOLUTIONS, VIDEO_DURATION_MIN, VIDEO_DURATION_MAX,
    MAX_VIDEO_REFERENCES,
)

from app.media.providers.video import (
    ResolvedVideoRequest, VideoProviderAdapter, VideoProviderCompleted,
    VideoProviderConfigurationError, VideoProviderError, VideoProviderPending,
    VideoProviderTerminalError,
)

MAX_VIDEO_BYTES = 100 * 1024 * 1024
DOWNLOAD_DEADLINE_SECONDS = 60
API_TIMEOUT = (5, 20)
DOWNLOAD_TIMEOUT = (5, 10)


class AlibabaWanVideoProvider(VideoProviderAdapter):
    DEFAULT_MODEL = "wan3.0-video"
    MODELS = frozenset({DEFAULT_MODEL})
    CAPABILITIES = ProviderMetadata("Alibaba Model Studio", (ModelMetadata(
        DEFAULT_MODEL, "Wan 3.0 Video", GenerationControls(
            aspect_ratios=list(VIDEO_ASPECT_RATIOS), resolutions=list(VIDEO_RESOLUTIONS),
            duration_seconds=IntegerRange(min=VIDEO_DURATION_MIN, max=VIDEO_DURATION_MAX),
            reference_roles=["first_frame"], max_references=MAX_VIDEO_REFERENCES,
            audio=True, count=IntegerRange(min=1, max=1), layout_types=["single"],
        ), technical=TechnicalCapabilities(
            input_media=("text", "image"), output_media=("video",),
            operations=("text_to_video", "image_to_video", "reference_image_conditioning",),
            execution=ExecutionBehavior(mode="asynchronous", polling_required=True),
            reference_inputs=(ReferenceInputSlot(name="first_frame", media_types=("image",), mime_types=("image/png", "image/jpeg", "image/webp"), roles=("first_frame",), max_count=1, max_bytes=10 * 1024 * 1024),),
            reference_mime_types=("image/png", "image/jpeg", "image/webp"),
            output_mime_types=("video/mp4",),
            max_prompt_characters=20000),
    ),))

    @staticmethod
    def configuration_is_valid(config: dict) -> bool:
        base_url = config.get("base_url")
        try:
            parsed = urlparse(base_url) if isinstance(base_url, str) else None
            return bool(
                parsed and parsed.scheme == "https" and parsed.hostname
                and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                and parsed.path in ("", "/")
                and isinstance(config.get("default_models") or {}, dict)
            )
        except ValueError:
            return False

    def __init__(self, *, api_key: str, base_url: str, model: str):
        if not isinstance(api_key, str) or not api_key.strip():
            raise VideoProviderConfigurationError("Video provider credential api_key is required")
        if not self.configuration_is_valid({"base_url": base_url}):
            raise VideoProviderConfigurationError("Video provider requires an HTTPS base_url origin")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = self.resolve_model(model)

    @classmethod
    def from_config(cls, *, credentials: dict[str, str], config: dict, model: str):
        return cls(api_key=credentials.get("api_key"), base_url=config.get("base_url"), model=model)

    @staticmethod
    def _identifier(value) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise VideoProviderError("Video provider returned an invalid operation identifier")
        return value

    def _api(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if method == "POST":
            headers.update({"Content-Type": "application/json", "X-DashScope-Async": "enable"})
        try:
            with requests.request(
                method, self.base_url + path, headers=headers, json=payload,
                timeout=API_TIMEOUT, allow_redirects=False,
            ) as response:
                if response.status_code in (400, 401, 403, 404, 422):
                    raise VideoProviderTerminalError("Video provider rejected the request")
                if response.status_code != 200:
                    raise VideoProviderError("Video provider HTTP request failed")
                body = response.json()
        except (requests.RequestException, ValueError):
            # Do not retain raw error text, URLs, response bodies or headers.
            raise VideoProviderError("Video provider HTTP response unavailable") from None
        if not isinstance(body, dict):
            raise VideoProviderError("Video provider returned an invalid response")
        if body.get("code"):
            raise VideoProviderTerminalError("Video provider rejected the operation")
        return body

    @staticmethod
    def _metadata(body: dict) -> dict:
        metadata = {}
        request_id = body.get("request_id")
        if isinstance(request_id, str) and re.fullmatch(r"[a-fA-F0-9-]{32,36}", request_id):
            metadata["request_id"] = request_id
        usage = body.get("usage")
        if isinstance(usage, dict):
            names = {"video_count": "video_count", "output_video_duration": "duration_seconds", "fps": "fps"}
            metadata["usage"] = {
                target: usage[key] for key, target in names.items()
                if type(usage.get(key)) in (int, float) and 0 <= usage[key] <= 10000
            }
        return metadata

    def submit(self, request: ResolvedVideoRequest) -> VideoProviderPending:
        if request.provider != "alibaba" or self.resolve_model(request.model) != self.model:
            raise VideoProviderConfigurationError("Video request does not match selected provider/model")
        if (
            not VIDEO_DURATION_MIN <= request.duration_seconds <= VIDEO_DURATION_MAX
            or request.aspect_ratio not in VIDEO_ASPECT_RATIOS
            or request.resolution not in VIDEO_RESOLUTIONS
            or len(request.references) > MAX_VIDEO_REFERENCES or len(request.description) > 20000
        ):
            raise VideoProviderConfigurationError("Unsupported video request")
        inputs = {"prompt": request.description}
        if request.references:
            reference = request.references[0]
            if reference.role != "first_frame" or reference.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise VideoProviderConfigurationError("Unsupported video reference")
            inputs["media"] = [{
                "type": "first_frame",
                "url": f"data:{reference.mime_type};base64,{base64.b64encode(reference.data).decode('ascii')}",
            }]
        body = self._api("POST", "/api/v1/services/aigc/video-generation/video-synthesis", {
            "model": self.model, "input": inputs,
            "parameters": {
                "resolution": request.resolution.upper(), "ratio": request.aspect_ratio,
                "duration": request.duration_seconds, "audio": request.audio,
            },
        })
        output = body.get("output") or {}
        if not isinstance(output, dict):
            raise VideoProviderError("Video provider returned an invalid submission")
        if output.get("task_status") not in {"PENDING", "RUNNING", "SUCCEEDED"}:
            raise VideoProviderTerminalError("Video provider could not start the operation")
        return VideoProviderPending("alibaba", self.model, self._identifier(output.get("task_id")), self._metadata(body))

    def poll(self, operation_id: str, checkpoint: dict) -> VideoProviderPending | VideoProviderCompleted:
        operation_id = self._identifier(operation_id)
        body = self._api("GET", f"/api/v1/tasks/{quote(operation_id, safe='')}")
        output = body.get("output") or {}
        if not isinstance(output, dict) or output.get("task_id") != operation_id:
            raise VideoProviderError("Video provider operation identity mismatch")
        status = output.get("task_status")
        if status in {"PENDING", "RUNNING"}:
            return VideoProviderPending("alibaba", self.model, operation_id, self._metadata(body))
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise VideoProviderTerminalError("Video provider operation terminated without output")
        if status != "SUCCEEDED":
            raise VideoProviderError("Video provider returned an unsupported operation state")
        data = self._download(output.get("video_url"))
        return VideoProviderCompleted("alibaba", self.model, data, len(data), self._metadata(body))

    @staticmethod
    def _download(url: str) -> bytes:
        parsed = urlparse(url) if isinstance(url, str) else None
        if (
            not parsed or parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".aliyuncs.com") or parsed.username or parsed.password
            or parsed.fragment
        ):
            raise VideoProviderTerminalError("Video provider returned an unsupported output location")
        deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
        try:
            # No API authorization on the storage request and no redirects.
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise VideoProviderError("Video output download failed")
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type not in {"video/mp4", "application/octet-stream"}:
                    raise VideoProviderTerminalError("Video output has an unsupported content type")
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > MAX_VIDEO_BYTES):
                    raise VideoProviderTerminalError("Video output exceeds the download limit")
                chunks, total = [], 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() > deadline:
                        raise VideoProviderError("Video download deadline exceeded")
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise VideoProviderTerminalError("Video output exceeds the download limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
        except requests.RequestException:
            raise VideoProviderError("Video output download unavailable") from None
        if len(data) < 12 or data[4:8] != b"ftyp":
            raise VideoProviderTerminalError("Video output is not an MP4 file")
        return data
