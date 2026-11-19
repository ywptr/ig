"""Standalone application contract tests. Cloud calls use mocked transports."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vercel.blob import BlobNotFoundError

from ig_storage import (
    Checksum, DriveClient, DriveError, GoogleDriveConfig, GoogleDriveStore,
    LocalConfig, LocalStore, ObjectStat, StorageRouter, VercelBlobConfig, VercelBlobStore,
)
from ig_storage.backends.drive_client import UPLOAD


class ConsumerTests(unittest.TestCase):
    def test_local_workflow_and_legacy_locator(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "objects"
            local = LocalStore(LocalConfig(root))
            router = StorageRouter({"file": local}, write_store=local)
            stored = router.put(filename="../report.bin", data=b"report")
            self.assertTrue(local.capabilities.streaming_read)
            self.assertEqual(stored.size_bytes, 6)
            self.assertEqual(router.stat(stored.locator), ObjectStat(size_bytes=6))
            self.assertEqual(list(router.iter_bytes(stored.locator, chunk_size=2)), [b"re", b"po", b"rt"])
            legacy = root / "old;report.bin"
            legacy.write_bytes(b"legacy")
            self.assertTrue(router.exists(legacy.as_uri()))
            self.assertEqual(b"".join(router.iter_bytes(legacy.as_uri())), b"legacy")
            outside = Path(directory) / "outside"
            outside.write_bytes(b"private")
            link = root / "link"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                router.stat(link.as_uri())
            router.delete(stored.locator)
            router.delete(stored.locator)
            self.assertIsNone(router.stat(stored.locator))

    def test_private_blob_configuration_and_stat(self):
        blob = VercelBlobStore(VercelBlobConfig(token="fake-blob", prefix="reports", read_timeout=12))
        upload = SimpleNamespace(pathname="reports/key-random")
        with patch("ig_storage.backends.vercel_blob.put", return_value=upload) as put:
            stored = blob.put(filename="private-name", data=b"abc")
        self.assertEqual(stored.locator, "vercel-blob:///reports/key-random")
        self.assertRegex(put.call_args.args[0], r"^reports/[a-f0-9]{32}$")
        self.assertEqual(put.call_args.kwargs, {"token": "fake-blob", "access": "private", "add_random_suffix": True})
        with patch("ig_storage.backends.vercel_blob.head",
                   return_value=SimpleNamespace(size=3, content_type="text/plain")) as head:
            self.assertEqual(blob.stat(stored.locator), ObjectStat(size_bytes=3, content_type="text/plain"))
        head.assert_called_once_with("reports/key-random", token="fake-blob")
        with patch("ig_storage.backends.vercel_blob.get", return_value=SimpleNamespace(content=b"abc")) as get:
            self.assertEqual(list(blob.iter_bytes(stored.locator, chunk_size=2)), [b"ab", b"c"])
        get.assert_called_once_with("reports/key-random", token="fake-blob", access="private", use_cache=False, timeout=12)
        self.assertFalse(blob.capabilities.streaming_read)
        with patch("ig_storage.backends.vercel_blob.delete", side_effect=BlobNotFoundError()):
            blob.delete(stored.locator)
        with patch("ig_storage.backends.vercel_blob.head", side_effect=BlobNotFoundError()):
            self.assertFalse(blob.exists(stored.locator))
        with patch("ig_storage.backends.vercel_blob.get") as get:
            with self.assertRaises(ValueError):
                list(blob.iter_bytes("vercel-blob:///https://untrusted.invalid/object"))
            get.assert_not_called()

    def test_drive_runtime_resumable_write_and_callback_routing(self):
        folder = {"mimeType": "application/vnd.google-apps.folder", "capabilities": {"canAddChildren": True}}
        md5 = "900150983cd24fb0d6963f7d28e17f72"
        responses = [
            self.response(body=folder), self.response(body={"ids": ["allocated"]}),
            self.response(headers={"Location": UPLOAD + "?upload_id=mock"}),
            self.response(201, {"id": "allocated"}),
            self.response(body={"size": "3", "mimeType": "text/plain", "md5Checksum": md5}),
            self.response(), self.response(204), self.response(404),
        ]
        responses[5].iter_content.return_value = [b"ab", b"c"]
        drive = GoogleDriveStore(GoogleDriveConfig("archive", "folder"), client=DriveClient("mock-token", timeout=14))
        resolve = Mock(return_value=drive)
        router = StorageRouter({"gdrive": resolve}, write_store=drive)
        with patch("ig_storage.backends.drive_client.requests.request", side_effect=responses) as request:
            stored = router.put(filename="report.bin", data=b"abc")
            resolve.assert_not_called()
            self.assertEqual(stored.locator, "gdrive://archive/allocated")
            self.assertEqual(router.stat(stored.locator), ObjectStat(3, "text/plain", Checksum("md5", md5)))
            self.assertEqual(list(router.iter_bytes(stored.locator, chunk_size=2)), [b"ab", b"c"])
            router.delete(stored.locator)
            self.assertIsNone(router.stat(stored.locator))
            with self.assertRaises(DriveError):
                router.stat("gdrive://other/allocated")
        self.assertEqual(request.call_count, 8)
        self.assertEqual(request.call_args_list[2].kwargs["json"]["parents"], ["folder"])
        self.assertEqual(request.call_args_list[3].kwargs["headers"]["Content-Range"], "bytes 0-2/3")
        self.assertTrue(request.call_args_list[5].kwargs["stream"])
        self.assertTrue(all(call.kwargs["timeout"] == 14 for call in request.call_args_list))
        self.assertTrue(all(call.kwargs["headers"]["Authorization"] == "Bearer mock-token" for call in request.call_args_list))
        responses[5].close.assert_called_once()
        self.assertTrue(drive.capabilities.resumable_write)

    @staticmethod
    def response(status=200, body=None, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.json.return_value = body or {}
        return response
