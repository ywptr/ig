"""Kling 4.0 Omni API translation with a bounded, image-only reference surface."""
import base64
from io import BytesIO
import re
from urllib.parse import urlsplit

import av
import requests
from PIL import Image

from ig_core.media.catalog import GenerationControls, IntegerRange, ModelMetadata, ProviderMetadata
from ig_core.media.technical import ExecutionBehavior, ReferenceInputSlot, TechnicalCapabilities
from ig_core.media.validation import MediaRequestValidationError, validate_resolved_video
from ig_core.providers.video import (
    ResolvedVideoRequest, VideoProviderAdapter, VideoProviderCompleted, VideoProviderConfigurationError,
    VideoProviderError, VideoProviderTerminalError, VideoProviderPending, VideoProviderRejected,
)
from ig_core.providers.video_download import download_mp4

BASE_URL = "https://api-singapore.klingai.com"
MODEL = "kling-4.0"
MIME_TYPES = ("image/jpeg", "image/png")
IMAGE_LIMIT = 10 * 1024 * 1024
TOTAL_IMAGE_LIMIT = 30 * 1024 * 1024
PENDING_STATES = ("submitted", "processing")
REFERENCE_ROLES = ("character", "style", "composition")


class KlingVideoProvider(VideoProviderAdapter):
    DEFAULT_MODEL = MODEL
    MODELS = frozenset({MODEL})
    CAPABILITIES = ProviderMetadata("Kling AI", (ModelMetadata(
        MODEL, "Kling 4.0", GenerationControls(
            aspect_ratios=["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"],
            resolutions=["720p", "1080p", "4k"], duration_seconds=IntegerRange(min=3, max=30),
            audio=True, native_audio_required=True, reference_roles=["first_frame", *REFERENCE_ROLES],
            max_references=10, count=IntegerRange(min=1, max=1), layout_types=["single"],
        ), technical=TechnicalCapabilities(
            input_media=("text", "image"), output_media=("video",),
            operations=("text_to_video", "image_to_video", "reference_to_video", "multi_reference_conditioning"),
            execution=ExecutionBehavior(mode="asynchronous", polling_required=True),
            reference_inputs=(
                ReferenceInputSlot(name="first_frame", media_types=("image",), mime_types=MIME_TYPES,
                                   roles=("first_frame",), max_count=1, max_bytes=IMAGE_LIMIT),
                ReferenceInputSlot(name="reference_images", media_types=("image",), mime_types=MIME_TYPES,
                                   roles=REFERENCE_ROLES, max_count=10, max_bytes=IMAGE_LIMIT),
            ), reference_mime_types=MIME_TYPES, output_mime_types=("video/mp4",),
            max_prompt_characters=11000,
        ),
    ),))

    @classmethod
    def configuration_is_valid(cls, config: dict) -> bool:
        return isinstance(config, dict) and config.get("base_url") in (None, BASE_URL, BASE_URL + "/")

    @classmethod
    def from_config(cls, *, credentials, config, model):
        key = credentials.get("api_key")
        if not isinstance(key, str) or not key or not key.isascii() or any(not 33 <= ord(char) <= 126 for char in key):
            raise VideoProviderConfigurationError("Kling requires a valid API key")
        if not cls.configuration_is_valid(config):
            raise VideoProviderConfigurationError("Kling requires the Singapore API origin")
        return cls(key, cls.resolve_model(model))

    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self.model = self.resolve_model(model)

    def _api(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            with requests.request(method, BASE_URL + path, headers=headers, timeout=(5, 20),
                                  allow_redirects=False, **kwargs) as response:
                status = response.status_code
                if status in (401, 403):
                    raise VideoProviderRejected("VIDEO_PROVIDER_ACCESS")
                if status in (400, 404, 422):
                    raise VideoProviderRejected("VIDEO_PROVIDER_INVALID_REQUEST")
                if status not in (200, 429):
                    raise VideoProviderError("Video provider HTTP request failed")
                body = response.json()
        except (requests.RequestException, ValueError):
            raise VideoProviderError("Video provider HTTP response unavailable") from None
        if not isinstance(body, dict) or type(body.get("code")) is not int:
            raise VideoProviderError("Invalid video provider response")
        code = body["code"]
        if code or status == 429:
            if 1000 <= code <= 1004 or 1100 <= code <= 1103 or code == 1304:
                raise VideoProviderRejected("VIDEO_PROVIDER_ACCESS")
            if 1200 <= code <= 1203 or code in (1300, 1301):
                raise VideoProviderRejected("VIDEO_PROVIDER_INVALID_REQUEST")
            if (status == 429 or code in (1302, 1303)) and method == "POST":
                raise VideoProviderRejected("VIDEO_PROVIDER_RATE_LIMIT")
            raise VideoProviderError("Video provider did not complete the request")
        return body

    def submit(self, request: ResolvedVideoRequest) -> VideoProviderPending:
        if request.provider != "kling" or request.model != self.model:
            raise VideoProviderConfigurationError("Video request does not match selected provider/model")
        validate_resolved_video(request, self.CAPABILITIES.models[0])
        if sum(len(item.data) for item in request.references) > TOTAL_IMAGE_LIMIT:
            raise MediaRequestValidationError("Combined reference images exceed the submission limit")
        indexed = list(enumerate(request.references, start=1))
        references = sorted(indexed, key=lambda item: item[1].role != "first_frame")
        prompt = re.sub(r"\breference image (\d{1,2})\b",
                        lambda match: f"@Image{match[1]}" if 1 <= int(match[1]) <= len(indexed) else match[0],
                        request.description)
        for index, reference in indexed:
            if reference.role in REFERENCE_ROLES:
                prompt += f"\nUse @Image{index} as a {reference.role} reference."
        if len(prompt) > self.CAPABILITIES.models[0].technical.max_prompt_characters:
            raise MediaRequestValidationError("Prompt and reference instructions exceed model limit")
        contents = [{"type": "prompt", "text": prompt}]
        for index, reference in references:
            self._validate_image(reference)
            contents.append({
                "type": "first_frame" if reference.role == "first_frame" else "refer_image",
                "url": base64.b64encode(reference.data).decode("ascii"),
                "id": f"Image{index}",
            })
        body = self._api("POST", f"/omni-video/{self.model}", json={
            "contents": contents,
            "settings": {"duration": request.duration_seconds, "aspect_ratio": request.aspect_ratio,
                         "resolution": request.resolution, "audio": "native"},
        })
        task = body.get("data")
        if not isinstance(task, dict):
            raise VideoProviderError("Video provider returned an invalid submission")
        operation_id = task.get("id")
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
            raise VideoProviderError("Video provider returned an invalid task identity")
        if task.get("status") == "failed":
            raise VideoProviderTerminalError("Video provider could not start the operation")
        if task.get("status") not in (*PENDING_STATES, "succeeded"):
            raise VideoProviderError("Video provider returned an unsupported operation state")
        return VideoProviderPending("kling", self.model, operation_id)

    def poll(self, operation_id: str, checkpoint: dict) -> VideoProviderPending | VideoProviderCompleted:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
            raise VideoProviderTerminalError("Invalid video provider task identity")
        body = self._api("GET", "/tasks", params={"task_ids": operation_id})
        tasks = body.get("data")
        if (not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict)
                or tasks[0].get("id") != operation_id):
            raise VideoProviderError("Video provider operation identity mismatch")
        task = tasks[0]
        status = task.get("status")
        if status in PENDING_STATES:
            return VideoProviderPending("kling", self.model, operation_id)
        if status != "succeeded":
            raise VideoProviderTerminalError("Video provider operation ended without a supported result")
        outputs = task.get("outputs")
        videos = [item for item in outputs if isinstance(item, dict) and item.get("type") == "video"] if isinstance(outputs, list) else []
        if len(videos) != 1:
            raise VideoProviderTerminalError("Video provider did not return exactly one video")
        data = self._download(videos[0].get("url"))
        try:
            with av.open(BytesIO(data), format="mp4") as container:
                if not container.streams.video:
                    raise ValueError
        except (av.FFmpegError, ValueError):
            raise VideoProviderTerminalError("Video output is not a readable MP4 video") from None
        return VideoProviderCompleted("kling", self.model, data, len(data))

    @staticmethod
    def _validate_image(reference):
        try:
            with Image.open(BytesIO(reference.data), formats=["JPEG", "PNG"]) as image:
                width, height = image.size
                if Image.MIME.get(image.format) != reference.mime_type or min(width, height) < 300 or not 0.4 <= width / height <= 2.5:
                    raise ValueError
                image.verify()
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            raise MediaRequestValidationError("Unsupported Kling reference image dimensions or format") from None

    @staticmethod
    def _download(url):
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (not parsed or parsed.scheme != "https" or not parsed.hostname
                    or not parsed.hostname.endswith(".klingai.com")
                    or parsed.port not in (None, 443) or parsed.username is not None
                    or parsed.password is not None or parsed.fragment):
                raise ValueError
        except ValueError:
            raise VideoProviderTerminalError("Video provider returned an unsupported output location") from None
        return download_mp4(url, max_bytes=100 * 1024 * 1024, deadline_seconds=60, timeout=(5, 10))
