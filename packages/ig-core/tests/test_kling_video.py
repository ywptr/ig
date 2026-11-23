import base64
from dataclasses import replace
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

import av
from PIL import Image

from ig_core.media.validation import MediaRequestValidationError
from ig_core.providers.kling.video import BASE_URL, MODEL, KlingVideoProvider
from ig_core.providers.video import (
    ResolvedVideoReference, ResolvedVideoRequest, VideoProviderConfigurationError,
    VideoProviderError, VideoProviderRejected, VideoProviderTerminalError,
)


def make_image():
    output = BytesIO()
    Image.new("RGB", (320, 320), "black").save(output, "PNG")
    return output.getvalue()


def make_video():
    output = BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), "black"))
        for _ in range(4):
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


class KlingTests(TestCase):
    def setUp(self):
        self.adapter = KlingVideoProvider.from_config(credentials={"api_key": "test-only"}, config={}, model=MODEL)
        self.request = ResolvedVideoRequest("A quiet street", "kling", MODEL, 5, "21:9", "4k", True)
        self.http = self.enterContext(patch("ig_core.providers.kling.video.requests.request"))
        self.response = self.http.return_value.__enter__.return_value
        self.response.status_code = 200

    def test_catalog_and_submission(self):
        operations = self.adapter.CAPABILITIES.models[0].technical.operations
        self.assertIn("reference_to_video", operations)
        self.assertNotIn("image_audio_to_video", operations)
        with self.assertRaises(VideoProviderConfigurationError):
            self.adapter.resolve_model("kling-4.0-flash")
        self.response.json.return_value = {"code": 0, "data": {"id": "42", "status": "submitted"}}
        self.assertEqual(self.adapter.submit(self.request).operation_id, "42")
        self.assertEqual(self.http.call_args.args, ("POST", BASE_URL + "/omni-video/" + MODEL))
        self.assertEqual(self.http.call_args.kwargs["json"], {
            "contents": [{"type": "prompt", "text": "A quiet street"}],
            "settings": {"duration": 5, "aspect_ratio": "21:9", "resolution": "4k", "audio": "native"},
        })
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer test-only")
        self.assertFalse(self.http.call_args.kwargs["allow_redirects"])

    def test_reference_translation_and_rejection(self):
        png = make_image()
        first = ResolvedVideoReference("first", "image/png", png)
        style = ResolvedVideoReference("style", "image/png", png, "style")
        self.response.json.return_value = {"code": 0, "data": {"id": "42", "status": "processing"}}
        self.adapter.submit(replace(self.request, references=[first]))
        self.assertEqual(self.http.call_args.kwargs["json"]["contents"][1]["type"], "first_frame")
        self.adapter.submit(replace(self.request, description="Use reference image 1", references=[style, first]))
        payload = self.http.call_args.kwargs["json"]["contents"]
        self.assertIn("@Image1", payload[0]["text"])
        self.assertEqual(payload[1], {"type": "first_frame", "url": base64.b64encode(png).decode(), "id": "Image2"})
        self.assertEqual(payload[2]["type"], "refer_image")
        self.http.reset_mock()
        for invalid in (
            replace(self.request, audio=False),
            replace(self.request, references=[ResolvedVideoReference("audio", "audio/wav", b"x", "voice_track")]),
            replace(self.request, references=[replace(first, data=b"incorrect")]),
            replace(self.request, references=[style] * 11),
            replace(self.request, references=[first, *([style] * 10)]),
        ):
            with self.assertRaises(MediaRequestValidationError):
                self.adapter.submit(invalid)
        self.http.assert_not_called()

    def test_poll_and_bounded_download(self):
        video = make_video()
        self.response.json.return_value = {"code": 0, "data": [{"id": "42", "status": "processing"}]}
        self.assertEqual(self.adapter.poll("42", {}).operation_id, "42")
        self.assertEqual(self.http.call_args.kwargs["params"], {"task_ids": "42"})
        url = "https://p2-kling.klingai.com/result.mp4?signature=test"
        self.response.json.return_value["data"][0].update(
            status="succeeded", outputs=[{"type": "video", "url": url}])
        with patch("ig_core.providers.video_download.requests.get") as get:
            response = get.return_value.__enter__.return_value
            response.status_code = 200
            response.headers = {"Content-Type": "video/mp4"}
            response.iter_content.return_value = [video]
            completed = self.adapter.poll("42", {})
            self.assertEqual((completed.data, completed.metadata), (video, {}))
            get.assert_called_once_with(url, stream=True, timeout=(5, 10), allow_redirects=False)
            response.iter_content.return_value = [b"0000ftypinvalid"]
            with self.assertRaises(VideoProviderTerminalError):
                self.adapter.poll("42", {})
        for target in ("http://p2-kling.klingai.com/a", "https://p2-kling.klingai.com.fake.test/a",
                       "https://p2-kling.klingai.com:444/a", "https://127.0.0.1/a"):
            with self.assertRaises(VideoProviderTerminalError):
                self.adapter._download(target)

    def test_terminal_and_transient_errors(self):
        for status in ("failed", "unknown"):
            self.response.json.return_value = {"code": 0, "data": [{"id": "42", "status": status, "message": "raw secret"}]}
            with self.assertRaisesRegex(VideoProviderTerminalError, "^Video provider operation ended"):
                self.adapter.poll("42", {})
        self.response.status_code = 429
        self.response.json.return_value = {"code": 1303}
        with self.assertRaisesRegex(VideoProviderRejected, "VIDEO_PROVIDER_RATE_LIMIT"):
            self.adapter.submit(self.request)
        with self.assertRaises(VideoProviderError) as failure:
            self.adapter.poll("42", {})
        self.assertNotIsInstance(failure.exception, VideoProviderTerminalError)
        self.response.status_code = 403
        with self.assertRaisesRegex(VideoProviderRejected, "VIDEO_PROVIDER_ACCESS"):
            self.adapter.submit(self.request)
        self.response.status_code = 503
        with self.assertRaises(VideoProviderError) as failure:
            self.adapter.submit(self.request)
        self.assertNotIsInstance(failure.exception, VideoProviderTerminalError)
