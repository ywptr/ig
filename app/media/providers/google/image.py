"""Transitional imports and IG model policy for GoogleImageProvider."""
import logging

from ig_core.providers.google.image import *  # noqa: F403
from ig_core.providers.google.image import GoogleImageProvider as CoreGoogleImageProvider

from app.media.providers.model_policy import ImageModelPolicy


class GoogleImageProvider(ImageModelPolicy, CoreGoogleImageProvider):
    logger = logging.getLogger(__name__)

    @staticmethod
    def configuration_is_valid(config: dict) -> bool:
        return isinstance(config, dict) and isinstance(config.get("default_models") or {}, dict)
