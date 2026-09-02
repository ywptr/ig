import base64
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


OUTPUT_DIR = Path("/app/output")


class OpenAIImageService:

    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"]
        )

        self.output_dir = OUTPUT_DIR

    def generate(self, prompt: str):
        result = self.client.images.generate(
            model="gpt-image-2",
            prompt=prompt,
        )

        image_data = result.data[0]

        if not image_data.b64_json:
            raise RuntimeError(
                "OpenAI did not return base64 image data"
            )

        image_bytes = base64.b64decode(
            image_data.b64_json
        )

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d-%H%M%S"
        )

        filename = f"{uuid.uuid4()}.png"
        filepath = OUTPUT_DIR / filename

        OUTPUT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        filepath.write_bytes(image_bytes)

        return {
            "status": "completed",
            "model": "gpt-image-2",
            "filename": filename,
            "size_bytes": len(image_bytes),
        }
