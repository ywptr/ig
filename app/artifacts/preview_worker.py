"""Deterministic codecs; image callable in-process, video isolated by main()."""
import json
import math
from pathlib import Path
import sys
import warnings

from app.artifacts.preview_policy import (
    IMAGE_EDGE, FRAME_SIZE, MAX_PIXELS, MAX_DIMENSION, MAX_VIDEO_SECONDS, MAX_PREVIEW_BYTES, frame_positions,
)


def dimensions(width, height):
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and width * height <= MAX_PIXELS):
        raise ValueError("Unsupported dimensions")


def image_preview(source, destination):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source, formats=["PNG", "JPEG", "WEBP"]) as original:
            dimensions(*original.size)
            original.seek(0)  # Animated inputs produce one static preview.
            image = ImageOps.exif_transpose(original)
            image.thumbnail((IMAGE_EDGE, IMAGE_EDGE), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            image.info.clear()
            image.save(destination, "WEBP", quality=75, method=4, exif=b"", icc_profile=b"")
    return {"width": image.width, "height": image.height, "frame_count": 1, "positions": []}


def video_preview(source, destination):
    import av
    from PIL import Image
    av.logging.set_level(av.logging.ERROR)
    # Only a local, bounded MP4 input is accepted. No playlists/network protocols
    # or MOV external data references are followed.
    with av.open(str(source), format="mov", options={"protocol_whitelist": "file,pipe", "enable_drefs": "0"}) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        dimensions(stream.codec_context.width, stream.codec_context.height)
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration / av.time_base)
        if not math.isfinite(duration) or not 0 < duration <= MAX_VIDEO_SECONDS:
            raise ValueError("Unsupported duration")
        positions = frame_positions(duration)
        frames = []
        for position in positions:
            offset = (stream.start_time or 0) + int(position / stream.time_base)
            container.seek(offset, stream=stream, backward=True, any_frame=False)
            selected = None
            for index, frame in enumerate(container.decode(stream)):
                if index >= 360:
                    raise ValueError("Decode budget exceeded")
                dimensions(frame.width, frame.height)
                # Decode from the preceding keyframe to the requested timestamp.
                selected = frame
                if frame.pts is not None and frame.pts >= offset:
                    break
            if selected is None:
                raise ValueError("No video frame")
            image = selected.to_image()
            rotation = int(stream.metadata.get("rotate", "0")) % 360
            if rotation in (90, 180, 270):
                image = image.rotate(-rotation, expand=True)
            image.thumbnail(FRAME_SIZE, Image.Resampling.LANCZOS)
            frames.append(image)
    columns = min(3, len(frames))
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGB", (columns * FRAME_SIZE[0], rows * FRAME_SIZE[1]), (20, 20, 20))
    for index, frame in enumerate(frames):
        left = (index % columns) * FRAME_SIZE[0] + (FRAME_SIZE[0] - frame.width) // 2
        top = (index // columns) * FRAME_SIZE[1] + (FRAME_SIZE[1] - frame.height) // 2
        sheet.paste(frame, (left, top))
    sheet.save(destination, "WEBP", quality=75, method=4)
    return {"width": sheet.width, "height": sheet.height, "frame_count": len(frames), "positions": positions}


def video_metadata(source):
    import av
    av.logging.set_level(av.logging.ERROR)
    with av.open(str(source), format="mov", options={"protocol_whitelist": "file,pipe", "enable_drefs": "0"}) as container:
        stream = container.streams.video[0]
        width, height = stream.codec_context.width, stream.codec_context.height
        dimensions(width, height)
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration / av.time_base)
        fps = float(stream.average_rate)
        if not math.isfinite(duration) or not 0 < duration <= MAX_VIDEO_SECONDS or not math.isfinite(fps) or not 0 < fps <= 1000:
            raise ValueError("Unsupported media timing")
        return {"width": width, "height": height, "duration_seconds": duration, "fps": fps}


def main():
    import resource
    # Apply limits before loading native codecs. The parent additionally enforces
    # a wall-clock deadline and kills the entire subprocess group on timeout.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PREVIEW_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        kind, source, destination = sys.argv[1:]
        if kind == "metadata":
            result = video_metadata(Path(source))
        else:
            result = image_preview(Path(source), Path(destination)) if kind == "thumbnail" else video_preview(Path(source), Path(destination))
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"exception_class": type(exc).__name__}))
        # Never serialize decoder diagnostics, filesystem paths or source data.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
