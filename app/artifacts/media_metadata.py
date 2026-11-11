"""Best-effort bounded ingest metadata; never decode media during listing."""
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import warnings

from app.artifacts.preview_policy import MAX_PIXELS, MAX_DIMENSION, MAX_VIDEO_BYTES


def inspect_media(data: bytes, media_type: str) -> dict:
    values = {"sha256": sha256(data).hexdigest()}
    try:
        if media_type == "image":
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
                    width, height = image.size
                    if 0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and width * height <= MAX_PIXELS:
                        values.update(width=width, height=height)
        elif media_type == "video" and len(data) <= MAX_VIDEO_BYTES:
            with TemporaryDirectory(prefix="ig-probe-") as directory:
                source = Path(directory) / "source.bin"
                source.write_bytes(data)
                process = subprocess.Popen([sys.executable, "-m", "app.artifacts.preview_worker", "metadata", str(source), "unused"],
                    cwd=Path(__file__).resolve().parents[2],
                    env={"PATH": os.defpath, "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    output, _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    return values
                if process.returncode == 0 and len(output) <= 4096:
                    metadata = json.loads(output)
                    if (0 < metadata["width"] <= MAX_DIMENSION and 0 < metadata["height"] <= MAX_DIMENSION
                        and metadata["width"] * metadata["height"] <= MAX_PIXELS
                        and all(math.isfinite(metadata[k]) and metadata[k] > 0 for k in ("duration_seconds", "fps"))):
                        values.update(metadata)
    except Exception:
        # Missing/invalid optional metadata must not discard an otherwise stored
        # provider result or alter legacy upload/signature validation behavior.
        pass
    return values
