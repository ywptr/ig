import json
import unittest
from unittest.mock import patch

import requests

from app.media.providers.alibaba.video import AlibabaWanVideoProvider
from app.media.providers.video import (
    ResolvedVideoReference, ResolvedVideoRequest, VideoProviderCompleted,
    VideoProviderPending, VideoProviderConfigurationError, VideoProviderError, VideoProviderTerminalError,
)
from tests.video_support import (
    BASE_URL, HTTPResponse, MP4, REQUEST_ID, TASK_ID, TEMP_VIDEO_URL, TEST_KEY, operation_response,
)


class AlibabaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AlibabaWanVideoProvider(api_key=TEST_KEY, base_url=BASE_URL, model="wan3.0-video")
        self.http = self.enterContext(patch("app.media.providers.alibaba.video.requests.request"))
        self.download = self.enterContext(patch("app.media.providers.alibaba.video.requests.get"))

    def request(self, **fields):
        return ResolvedVideoRequest(**{
            "description": "A slow sunrise", "provider": "alibaba", "model": "wan3.0-video",
            "duration_seconds": 5, "aspect_ratio": "9:16", "resolution": "720p", "audio": False,
            **fields,
        })

    def test_submission_translates_generic_controls_and_uses_auth_header_only(self):
        self.http.return_value = operation_response()
        result = self.adapter.submit(self.request())
        self.assertIsInstance(result, VideoProviderPending)
        self.assertEqual(result.operation_id, TASK_ID)
        call = self.http.call_args
        self.assertEqual(call.args, ("POST", BASE_URL + "/api/v1/services/aigc/video-generation/video-synthesis"))
        self.assertEqual(call.kwargs["json"], {
            "model": "wan3.0-video", "input": {"prompt": "A slow sunrise"},
            "parameters": {"resolution": "720P", "ratio": "9:16", "duration": 5, "audio": False},
        })
        self.assertEqual(call.kwargs["headers"]["Authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(call.kwargs["headers"]["X-DashScope-Async"], "enable")
        self.assertNotIn(TEST_KEY, json.dumps(call.kwargs["json"]))
        self.assertNotIn(TEST_KEY, json.dumps(result.metadata))
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(call.kwargs["timeout"], (5, 20))

    def test_first_frame_becomes_data_uri_without_private_storage_url(self):
        self.http.return_value = operation_response()
        self.adapter.submit(self.request(references=[ResolvedVideoReference("artifact", "image/png", b"png")]))
        media = self.http.call_args.kwargs["json"]["input"]["media"]
        self.assertEqual(media, [{"type": "first_frame", "url": "data:image/png;base64,cG5n"}])

    def test_all_generic_resolutions_and_audio_map_inside_adapter(self):
        self.http.return_value = operation_response()
        for resolution in ("480p", "720p", "1080p"):
            with self.subTest(resolution=resolution):
                self.adapter.submit(self.request(resolution=resolution, audio=True))
                parameters = self.http.call_args.kwargs["json"]["parameters"]
                self.assertEqual(parameters["resolution"], resolution.upper())
                self.assertTrue(parameters["audio"])

    def test_pending_and_running_poll_same_id_without_submission(self):
        for state in ("PENDING", "RUNNING"):
            with self.subTest(state=state):
                self.http.return_value = operation_response(state)
                result = self.adapter.poll(TASK_ID, {})
                self.assertIsInstance(result, VideoProviderPending)
                self.assertEqual(result.operation_id, TASK_ID)
                self.assertEqual(self.http.call_args.args, ("GET", BASE_URL + f"/api/v1/tasks/{TASK_ID}"))
                self.assertNotIn("X-DashScope-Async", self.http.call_args.kwargs["headers"])
        self.download.assert_not_called()

    def test_success_downloads_incrementally_and_returns_sanitized_mp4(self):
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        response = HTTPResponse(chunks=[MP4[:7], MP4[7:]])
        self.download.return_value = response
        result = self.adapter.poll(TASK_ID, {})
        self.assertIsInstance(result, VideoProviderCompleted)
        self.assertEqual((result.data, result.size_bytes), (MP4, len(MP4)))
        self.assertEqual((result.media_type, result.mime_type, result.extension), ("video", "video/mp4", "mp4"))
        self.assertTrue(response.iterated)
        self.assertEqual(result.metadata, {
            "request_id": REQUEST_ID, "usage": {"video_count": 1, "duration_seconds": 5, "fps": 30},
        })
        self.assertNotIn("signature", json.dumps(result.metadata))
        self.assertNotIn(TEST_KEY, json.dumps(result.metadata))
        self.assertEqual(self.download.call_args.kwargs, {
            "stream": True, "timeout": (5, 10), "allow_redirects": False,
        })

    def test_failed_canceled_unknown_are_terminal_and_do_not_download(self):
        for state in ("FAILED", "CANCELED", "UNKNOWN"):
            with self.subTest(state=state):
                self.http.return_value = operation_response(state, message=TEST_KEY, code="private")
                with self.assertRaises(VideoProviderTerminalError) as error:
                    self.adapter.poll(TASK_ID, {})
                self.assertNotIn(TEST_KEY, str(error.exception))
        self.download.assert_not_called()
        self.assertTrue(all(call.args[0] == "GET" for call in self.http.call_args_list))

    def test_identity_mismatch_and_invalid_status_fail_safely(self):
        for response in (operation_response("RUNNING", task_id="other"), operation_response("NEW_STATE")):
            self.http.return_value = response
            with self.assertRaises(VideoProviderError):
                self.adapter.poll(TASK_ID, {})
        self.download.assert_not_called()

    def test_http_errors_and_exception_messages_are_sanitized(self):
        for status in (401, 429, 500):
            with self.subTest(status=status):
                self.http.return_value = HTTPResponse({"message": TEST_KEY}, status=status)
                with self.assertRaises(VideoProviderError) as error:
                    self.adapter.submit(self.request())
                self.assertNotIn(TEST_KEY, str(error.exception))
        self.http.side_effect = requests.ConnectionError(TEST_KEY + TEMP_VIDEO_URL)
        with self.assertRaises(VideoProviderError) as error:
            self.adapter.submit(self.request())
        self.assertNotIn(TEST_KEY, str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_download_cap_applies_to_declared_and_actual_bytes(self):
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        for response in (
            HTTPResponse(headers={"Content-Type": "video/mp4", "Content-Length": "1000"}),
            HTTPResponse(chunks=[b"x" * 6, b"y" * 6]),
        ):
            with self.subTest(headers=response.headers):
                self.download.return_value = response
                with patch("app.media.providers.alibaba.video.MAX_VIDEO_BYTES", 10):
                    with self.assertRaises(VideoProviderTerminalError):
                        self.adapter.poll(TASK_ID, {})

    def test_download_deadline_http_mime_and_file_signature_limits(self):
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        for response in (
            HTTPResponse(status=403), HTTPResponse(headers={"Content-Type": "text/html"}),
            HTTPResponse(chunks=[b"not a video"]), HTTPResponse(chunks=[]),
        ):
            with self.subTest(status=response.status_code, headers=response.headers):
                self.download.return_value = response
                with self.assertRaises(VideoProviderError):
                    self.adapter.poll(TASK_ID, {})
        self.download.return_value = HTTPResponse()
        with patch("app.media.providers.alibaba.video.time.monotonic", side_effect=[0, 100]):
            with self.assertRaisesRegex(VideoProviderError, "deadline"):
                self.adapter.poll(TASK_ID, {})

    def test_untrusted_download_locations_are_rejected_without_request(self):
        for url in ("http://video.aliyuncs.com/file", "https://localhost/file", "https://evil.test/file"):
            self.http.return_value = operation_response("SUCCEEDED", video_url=url)
            with self.assertRaises(VideoProviderTerminalError):
                self.adapter.poll(TASK_ID, {})
        self.download.assert_not_called()

    def test_missing_key_and_invalid_origin_fail_without_http(self):
        for key, base in (("", BASE_URL), (TEST_KEY, "http://bad.test"), (TEST_KEY, "https://host/?secret=x")):
            with self.assertRaises(VideoProviderConfigurationError):
                AlibabaWanVideoProvider(api_key=key, base_url=base, model="wan3.0-video")
        self.http.assert_not_called()
