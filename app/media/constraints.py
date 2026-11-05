"""Shared IG media constraints; provider HTTP mechanics do not belong here."""
from typing import Literal, get_args

IMAGE_ASPECT_RATIOS = ("2:3", "4:5", "1:1", "16:9")
VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1")
VIDEO_DURATION_MIN = 2
VIDEO_DURATION_MAX = 30
VideoResolution = Literal["480p", "720p", "1080p"]
VIDEO_RESOLUTIONS = get_args(VideoResolution)
MAX_IMAGE_REFERENCES = 8
MAX_VIDEO_REFERENCES = 1
EXPLICIT_SELECTION_LEVELS = ("hard",)
