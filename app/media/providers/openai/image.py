"""Transitional imports and IG model policy for OpenAIImageProvider."""
from ig_core.providers.openai.image import *  # noqa: F403
from ig_core.providers.openai.image import OpenAIImageProvider as CoreOpenAIImageProvider

from app.media.providers.model_policy import ImageModelPolicy


class OpenAIImageProvider(ImageModelPolicy, CoreOpenAIImageProvider):
    pass
