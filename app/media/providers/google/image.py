"""Single-turn Gemini image generation. All Google wire semantics stay here."""
import base64
import binascii
import json
import logging
import re
import time
from dataclasses import replace
from typing import get_args

import requests

from app.media.capability_schemas import GenerationControls, IntegerRange, ModelMetadata, ProviderMetadata
from app.media.constraints import IMAGE_ASPECT_RATIOS
from app.media.prompts import STYLE_INSTRUCTIONS, build_image_prompt
from app.media.providers.base import (
    ImageProviderAdapter, ImageProviderConfigurationError, ImageProviderError,
    ImageProviderResult, ResolvedImageRequest,
)
from app.media.schemas import GridArrangement, MediaReference, ReferenceRole

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
API_TIMEOUT = (5, 90)
API_DEADLINE_SECONDS = 120
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
SUPPORTED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_ERROR_BYTES = 16 * 1024
MAX_ERROR_MESSAGE_CHARS = 256
logger = logging.getLogger(__name__)


class GoogleImageHTTPError(ImageProviderError):
    """Only allowlisted, sanitized diagnostics; no response/request retained."""
    def __init__(self, http_status: int, *, code: int | None = None,
                 status: str | None = None, message: str | None = None):
        self.http_status = http_status
        self.code = code
        self.status = status
        self.message = message
        super().__init__(f"Google image provider HTTP {http_status}"
                         f" code={code} status={status} message={message}")


def _safe_error_message(message: str, *, api_key: str, payload: dict) -> str:
    # Drop messages that look like echoed wire payloads or authentication headers.
    if re.search(r"authorization|x-goog-api-key|inlineData|contents|data:image|api[_ -]?key", message, re.I):
        return "[redacted provider message]"
    message = message.replace(api_key, "[redacted]")

    def redact_values(value):
        nonlocal message
        if isinstance(value, dict):
            for child in value.values():
                redact_values(child)
        elif isinstance(value, list):
            for child in value:
                redact_values(child)
        elif isinstance(value, str) and value:
            message = message.replace(value, "[redacted]")

    redact_values(payload)
    # Also remove unknown tokens/base64 and control characters before truncation.
    message = re.sub(r"[A-Za-z0-9_+/=-]{40,}", "[redacted]", message)
    message = " ".join(message.split())
    message = "".join(char for char in message if char.isprintable())
    return message[:MAX_ERROR_MESSAGE_CHARS]



class GoogleImageProvider(ImageProviderAdapter):
    DEFAULT_MODEL = "gemini-3.1-flash-lite-image"
    MODELS = {DEFAULT_MODEL: ModelMetadata(
        DEFAULT_MODEL, "Gemini 3.1 Flash-Lite Image", GenerationControls(
            aspect_ratios=list(IMAGE_ASPECT_RATIOS), resolutions=["1k"],
            reference_roles=list(get_args(ReferenceRole)), max_references=1,
            reference_strengths=list(get_args(MediaReference.model_fields["strength"].annotation)),
            count=IntegerRange(min=1, max=1), layout_types=["single", "panel_grid"],
            panel_counts=[4], panel_arrangements=list(get_args(GridArrangement)),
            panel_arrangements_selectable_in=["hard"],
            quality_profiles=["draft", "standard", "high"], styles=["auto", *STYLE_INSTRUCTIONS],
        ), use_cases=("thumbnail", "draft", "preview"),
    )}
    CAPABILITIES = ProviderMetadata("Google Gemini", tuple(MODELS.values()))

    def __init__(self, *, api_key: str):
        if not isinstance(api_key, str) or not api_key.strip() or "\n" in api_key or "\r" in api_key:
            raise ImageProviderConfigurationError("Image provider credential api_key is required")
        self._api_key = api_key

    @classmethod
    def from_config(cls, *, credentials: dict[str, str], config: dict):
        return cls(api_key=credentials.get("api_key"))

    @staticmethod
    def configuration_is_valid(config: dict) -> bool:
        return isinstance(config, dict) and isinstance(config.get("default_models") or {}, dict)

    @classmethod
    def tenant_default_model(cls, config: dict) -> str | None:
        if not cls.configuration_is_valid(config):
            raise ImageProviderConfigurationError("Invalid image model defaults")
        return (config.get("default_models") or {}).get("image") or config.get("default_model")

    @classmethod
    def resolve_tenant_model(cls, config: dict, explicit_model: str | None = None) -> str:
        if not cls.configuration_is_valid(config):
            raise ImageProviderConfigurationError("Invalid image model defaults")
        model = explicit_model or cls.tenant_default_model(config) or cls.DEFAULT_MODEL
        if not isinstance(model, str) or model not in cls.MODELS:
            raise ImageProviderConfigurationError("Unsupported image model")
        return model

    @classmethod
    def default_model_for(cls, config: dict) -> str:
        return cls.resolve_tenant_model(config)

    @classmethod
    def validate_submission_options(
        cls, *, config: dict, model: str | None, count: int, strategy: str,
        reference_count: int, parameters: dict,
    ) -> None:
        selected = cls.resolve_tenant_model(config, model)
        controls = cls.MODELS[selected].capabilities
        if (not controls.count.min <= count <= controls.count.max or strategy != "single"
            or reference_count > controls.max_references or parameters):
            raise ImageProviderConfigurationError("Unsupported image submission options")

    def _request(self, model: str, payload: dict) -> dict:
        # No retries: a timeout may follow a billable successful generation.
        deadline = time.monotonic() + API_DEADLINE_SECONDS
        try:
            with requests.post(
                f"{API_ROOT}/{model}:generateContent", json=payload,
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                timeout=API_TIMEOUT, allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200:
                    raise self._http_error(response, payload, deadline) from None
                chunks, total = [], 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() > deadline:
                        raise ImageProviderError("Image provider response deadline exceeded")
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ImageProviderError("Image provider response exceeds the supported size")
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks))
        except (requests.RequestException, ValueError):
            raise ImageProviderError("Image provider response unavailable") from None
        if not isinstance(body, dict) or body.get("error"):
            raise ImageProviderError("Image provider returned an invalid response")
        return body

    def _http_error(self, response, payload: dict, deadline: float) -> GoogleImageHTTPError:
        code = status = message = None
        chunks, total = [], 0
        try:
            # Read at most the budget plus a single byte to detect truncation.
            for chunk in response.iter_content(chunk_size=1024):
                if time.monotonic() > deadline:
                    break
                remaining = MAX_ERROR_BYTES + 1 - total
                chunks.append(chunk[:remaining])
                total += len(chunks[-1])
                if total > MAX_ERROR_BYTES:
                    break
            if total <= MAX_ERROR_BYTES and time.monotonic() <= deadline:
                body = json.loads(b"".join(chunks))
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    if type(error.get("code")) is int and 100 <= error["code"] <= 599:
                        code = error["code"]
                    candidate = error.get("status")
                    if (isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{1,64}", candidate)
                            and self._api_key not in candidate):
                        status = candidate
                    if isinstance(error.get("message"), str):
                        message = _safe_error_message(error["message"], api_key=self._api_key, payload=payload)
        except (requests.RequestException, ValueError, RecursionError):
            # HTTP status remains useful even if reading/parsing the error fails.
            pass
        failure = GoogleImageHTTPError(response.status_code, code=code, status=status, message=message)
        logger.warning("google.image.http_error %s", failure)
        return failure

    @staticmethod
    def _image(body: dict) -> tuple[str, bytes]:
        feedback = body.get("promptFeedback") or {}
        if not isinstance(feedback, dict) or feedback.get("blockReason"):
            raise ImageProviderError("Image provider did not return a usable image")
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ImageProviderError("Image provider did not return one image candidate")
        candidate = candidates[0]
        if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
            raise ImageProviderError("Image provider did not complete image generation")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ImageProviderError("Image provider returned invalid image content")
        images = [part["inlineData"] for part in parts if isinstance(part, dict)
                  and not part.get("thought") and "inlineData" in part]
        if len(images) != 1 or not isinstance(images[0], dict):
            raise ImageProviderError("Image provider did not return exactly one image")
        image = images[0]
        mime_type, encoded = image.get("mimeType"), image.get("data")
        if not isinstance(mime_type, str) or mime_type not in SUPPORTED_MIME_TYPES or not isinstance(encoded, str):
            raise ImageProviderError("Image provider returned unsupported image data")
        if not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ImageProviderError("Image provider returned invalid image data size")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ImageProviderError("Image provider returned invalid image encoding") from None
        signatures = {
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": data.startswith(b"\xff\xd8\xff"),
            "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        }
        if not data or len(data) > MAX_IMAGE_BYTES or not signatures[mime_type]:
            raise ImageProviderError("Image provider returned invalid image bytes")
        return mime_type, data

    def generate(self, request: ResolvedImageRequest) -> ImageProviderResult:
        model = self.resolve_tenant_model({}, request.model)
        controls = self.MODELS[model].capabilities
        resolved = replace(request, model=model, aspect_ratio=request.aspect_ratio or "2:3")
        if (
            request.provider != "google" or not request.description.strip() or request.parameters
            or resolved.aspect_ratio not in controls.aspect_ratios
            or request.quality not in controls.quality_profiles or request.style not in controls.styles
            or request.layout_type not in controls.layout_types
            or len(request.references) > controls.max_references
            or (request.layout_type == "panel_grid" and (
                request.panel_count not in controls.panel_counts or request.arrangement not in controls.panel_arrangements
            ))
        ):
            raise ImageProviderConfigurationError("Unsupported image request")
        parts = [{"text": build_image_prompt(resolved)}]
        # Quality is semantic only. Every profile uses the same 1K tier.
        if request.quality == "draft":
            parts[0]["text"] += "\nUse a simple, readable draft composition."
        elif request.quality == "high":
            parts[0]["text"] += "\nPrioritize careful composition and visual detail within the same image size."
        for reference in request.references:
            if (reference.mime_type not in SUPPORTED_MIME_TYPES
                or not 0 < len(reference.data) <= MAX_REFERENCE_BYTES
                or reference.role not in controls.reference_roles
                or reference.strength not in controls.reference_strengths):
                raise ImageProviderConfigurationError("Unsupported image reference")
            parts.append({"inlineData": {
                "mimeType": reference.mime_type, "data": base64.b64encode(reference.data).decode("ascii"),
            }})
        body = self._request(model, {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "candidateCount": 1, "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": resolved.aspect_ratio, "imageSize": "1K"},
            },
        })
        mime_type, data = self._image(body)
        metadata = {"aspect_ratio": resolved.aspect_ratio, "resolution": "1k",
                    "quality_profile": request.quality, "reference_count": len(request.references)}
        response_id = body.get("responseId")
        if isinstance(response_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", response_id) and self._api_key not in response_id:
            metadata["request_id"] = response_id
        usage = body.get("usageMetadata")
        if isinstance(usage, dict):
            metadata["usage"] = {target: usage[source] for source, target in (
                ("promptTokenCount", "input_tokens"), ("candidatesTokenCount", "output_tokens"),
                ("totalTokenCount", "total_tokens"),
            ) if type(usage.get(source)) is int and 0 <= usage[source] <= 10**9}
        return ImageProviderResult("google", model, mime_type, data, len(data), metadata)
