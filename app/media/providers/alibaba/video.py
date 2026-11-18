"""Transitional imports and IG model policy for AlibabaWanVideoProvider."""
from ig_core.providers.alibaba.video import *  # noqa: F403
from ig_core.providers.alibaba.video import AlibabaWanVideoProvider as CoreAlibabaWanVideoProvider

from app.media.providers.model_policy import VideoModelPolicy


class AlibabaWanVideoProvider(VideoModelPolicy, CoreAlibabaWanVideoProvider):
    pass
