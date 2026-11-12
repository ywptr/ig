"""Storage isolation using real disposable files, SQLite, and mocked providers."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import func, select

from tests import test_media_job_contracts as contracts
from tests import test_video_generation as video
from tests.video_support import MP4, TEMP_VIDEO_URL, HTTPResponse, operation_response
from app.artifacts import service
from app.artifacts.storage.local import LocalArtifactStore
from app.artifacts.storage.router import RoutingArtifactStore
from app.artifacts.storage.vercel_blob import VercelBlobArtifactStore
from app.db.models import Artifact, Job
from app.jobs.continuation import continue_job
from app.jobs.worker import execute_job

PNG = b"\x89PNG\r\n\x1a\n"


def local_store(test):
    directory = test.enterContext(TemporaryDirectory(prefix="ig-storage-"))
    return LocalArtifactStore(Path(directory) / "objects")


def install_storage(test):
    store = local_store(test)
    test.enterContext(patch("app.artifacts.service.SessionLocal", test.sessions))
    test.enterContext(patch("app.artifacts.service.artifact_store", store))
    test.enterContext(patch("app.artifacts.previews.artifact_store", store))
    test.enterContext(patch("app.api.artifacts.artifact_store", store))
    return store


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.store = local_store(self)

    def test_duplicate_names_and_traversal_names_are_only_hints(self):
        outside = self.store.base_dir.parent / "outside.png"
        names = ["same.png", "same.png", "../outside.png", str(outside),
                 "nested/../../outside.png", r"..\outside.png", "", "\x00"]
        uris = []
        for index, filename in enumerate(names):
            data = str(index).encode()
            uri = self.store.put_bytes(filename=filename, data=data)
            uris.append(uri)
            self.assertEqual(b"".join(self.store.iter_bytes(uri)), data)
            self.assertEqual(Path(uri.removeprefix("file://")).parent, self.store.base_dir)
        self.assertEqual(len(set(uris)), len(names))
        self.assertEqual(b"".join(self.store.iter_bytes(uris[0])), b"0")
        self.assertFalse(outside.exists())

    def test_key_collision_never_overwrites_existing_object(self):
        existing = UUID(int=1)
        fresh = UUID(int=2)
        with patch("app.artifacts.storage.local.uuid4", side_effect=[existing, existing, fresh]):
            first = self.store.put_bytes(filename="first", data=b"first")
            second = self.store.put_bytes(filename="second", data=b"second")
        self.assertNotEqual(first, second)
        self.assertEqual(b"".join(self.store.iter_bytes(first)), b"first")

    def test_legacy_nested_and_escaped_uris_remain_readable(self):
        legacy = self.store.base_dir / "old directory" / "reference #1.png"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(PNG)
        self.assertTrue(self.store.exists(legacy.as_uri()))
        self.assertEqual(b"".join(self.store.iter_bytes(legacy.as_uri(), chunk_size=3)), PNG)

    def test_invalid_locators_cannot_read_outside_root(self):
        outside = self.store.base_dir.parent / "secret"
        outside.write_bytes(b"private")
        root = self.store.base_dir
        self.store.put_bytes(filename="initialize", data=b"safe")
        for uri in (
            outside.as_uri(), (root.parent / "objects-other" / "secret").as_uri(),
            f"{root.as_uri()}/../secret", f"{root.as_uri()}/%2e%2e/secret",
            f"{root.as_uri()}/%2e%2e%2fsecret", f"{root.as_uri()}/bad%00name",
            f"{root.as_uri()}/bad%ZZname", root.as_uri(), "file:relative",
            "file://remote/etc/passwd", "https://example.test/file", "file:///etc/passwd",
            f"{root.as_uri()}/object?query=1", f"{root.as_uri()}/object#fragment", "file:///tmp/\nsecret",
        ):
            with self.subTest(uri=uri):
                with self.assertRaises(ValueError):
                    self.store.exists(uri)
                with self.assertRaises(ValueError):
                    list(self.store.iter_bytes(uri))

    def test_symlink_files_and_directories_are_not_followed(self):
        self.store.put_bytes(filename="initialize", data=b"safe")
        outside = self.store.base_dir.parent / "secret"
        outside.write_bytes(b"private")
        link = self.store.base_dir / "link"
        link.symlink_to(outside)
        directory = self.store.base_dir / "directory"
        directory.symlink_to(outside.parent, target_is_directory=True)
        for path in (link, directory / "secret"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                list(self.store.iter_bytes(path.as_uri()))
        # An existing symlink with a colliding random name is never overwritten.
        key = UUID(int=1)
        (self.store.base_dir / key.hex).symlink_to(outside)
        with patch("app.artifacts.storage.local.uuid4", side_effect=[key, UUID(int=2)]):
            self.store.put_bytes(filename="link", data=b"new")
        self.assertEqual(outside.read_bytes(), b"private")

    def test_failed_local_write_removes_partial_object(self):
        # Force a failure after exclusive creation, while the real file context
        # still owns/closes the descriptor.
        with self.assertRaises(TypeError):
            self.store.put_bytes(filename="reference.png", data="invalid byte payload")
        self.assertEqual(list(self.store.base_dir.iterdir()), [])

    def test_replaced_root_ancestor_cannot_redirect_reads_or_writes(self):
        parent = self.store.base_dir.parent / "parent"
        parent.mkdir()
        store = LocalArtifactStore(parent / "objects")
        uri = store.put_bytes(filename="safe.png", data=b"original")
        parent.rename(parent.with_name("original-parent"))
        outside = self.store.base_dir.parent / "outside"
        (outside / "objects").mkdir(parents=True)
        (outside / "objects" / Path(uri).name).write_bytes(b"private")
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            list(store.iter_bytes(uri))
        with self.assertRaises(OSError):
            store.put_bytes(filename="new.png", data=b"new")
        self.assertEqual(len(list((outside / "objects").iterdir())), 1)

    def test_configured_development_symlink_pins_trusted_root(self):
        self.store.base_dir.mkdir()
        alias = self.store.base_dir.parent / "development-link"
        alias.symlink_to(self.store.base_dir, target_is_directory=True)
        store = LocalArtifactStore(alias)
        uri = store.put_bytes(filename="display.png", data=PNG)
        self.assertEqual(b"".join(store.iter_bytes(uri)), PNG)
        self.assertEqual(store.base_dir, self.store.base_dir)

    def test_missing_content_returns_false(self):
        self.assertFalse(self.store.exists((self.store.base_dir / "missing").as_uri()))

    def test_non_regular_content_is_rejected(self):
        self.store.base_dir.mkdir()
        folder = self.store.base_dir / "folder"
        folder.mkdir()
        with self.assertRaises(ValueError):
            self.store.exists(folder.as_uri())


class VercelStorageTests(unittest.TestCase):
    def test_opaque_keys_and_private_access_preserve_adapter_behavior(self):
        store = VercelBlobArtifactStore()
        def uploaded(pathname, data, **options):
            self.assertTrue(options["add_random_suffix"])
            self.assertEqual(options["access"], "private")
            return SimpleNamespace(pathname=pathname + "-random")
        with patch("app.artifacts.storage.vercel_blob.put", side_effect=uploaded) as put:
            first = store.put_bytes(filename="private-display-name.png", data=b"first")
            second = store.put_bytes(filename="../../private-display-name.png", data=b"second")
        self.assertNotEqual(first, second)
        for call in put.call_args_list:
            self.assertRegex(call.args[0], r"^artifacts/[a-f0-9]{32}$")
        with patch("app.artifacts.storage.vercel_blob.head") as head, patch(
            "app.artifacts.storage.vercel_blob.get", return_value=SimpleNamespace(content=b"original"),
        ) as get:
            old = "vercel-blob:///artifacts/old-name.png"
            self.assertTrue(store.exists(old))
            self.assertEqual(b"".join(store.iter_bytes(old, chunk_size=2)), b"original")
            head.assert_called_once_with("artifacts/old-name.png")
            get.assert_called_once_with("artifacts/old-name.png", access="private", use_cache=False)

    def test_router_reads_historical_local_objects_when_write_provider_changes(self):
        local = local_store(self)
        uri = local.put_bytes(filename="legacy.png", data=PNG)
        remote = VercelBlobArtifactStore()
        router = RoutingArtifactStore(write_store=remote, local_store=local, vercel_blob_store=remote)
        self.assertTrue(router.exists(uri))
        self.assertEqual(b"".join(router.iter_bytes(uri)), PNG)


class ArtifactUploadTests(contracts.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.storage = install_storage(self)

    def upload(self, data=PNG, filename="reference.png", host="testserver", mime="image/png"):
        response = self.client.post("/v2/artifacts/uploads", headers={"host": host},
                                    files={"file": (filename, data, mime)})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["filename"], filename)
        self.assertNotIn("storage_uri", response.json())
        return response.json()["artifact_id"]

    def assert_content(self, artifact_id, expected, host="testserver"):
        response = self.client.get(f"/v2/artifacts/{artifact_id}/content", headers={"host": host})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, expected)

    def test_same_user_same_filename_retains_both_objects(self):
        first = self.upload(PNG + b"first")
        second = self.upload(PNG + b"second")
        self.assertNotEqual(first, second)
        self.assert_content(first, PNG + b"first")
        self.assert_content(second, PNG + b"second")
        with self.sessions() as db:
            self.assertNotEqual(db.get(Artifact, first).storage_uri, db.get(Artifact, second).storage_uri)

    def test_two_users_same_filename_isolation(self):
        first = self.upload(PNG + b"one")
        self.user_id = "user-two"
        second = self.upload(PNG + b"two")
        self.assert_content(second, PNG + b"two")
        for suffix in ("", "/content"):
            self.assertEqual(self.client.get(f"/v2/artifacts/{first}{suffix}").status_code, 404)
        self.user_id = "user-one"
        self.assert_content(first, PNG + b"one")
        for suffix in ("", "/content"):
            self.assertEqual(self.client.get(f"/v2/artifacts/{second}{suffix}").status_code, 404)

    def test_two_tenants_same_filename_isolation(self):
        first = self.upload(PNG + b"one")
        second = self.upload(PNG + b"two", host="other.test")
        self.assert_content(first, PNG + b"one")
        self.assert_content(second, PNG + b"two", host="other.test")
        for artifact_id, host in ((first, "other.test"), (second, "testserver")):
            for suffix in ("", "/content"):
                self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}{suffix}", headers={"host": host}).status_code, 404)

    def test_public_metadata_and_listing_hide_locator(self):
        artifact_id = self.upload()
        with self.sessions() as db:
            locator = db.get(Artifact, artifact_id).storage_uri
        for path in ("/v2/artifacts", f"/v2/artifacts/{artifact_id}"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(locator, response.text)
            self.assertNotIn("storage_uri", response.text)
        self.assert_content(artifact_id, PNG)

    def test_upload_mime_signature_and_size_limits_preserved(self):
        jpeg = b"\xff\xd8\xffjpeg"
        webp = b"RIFF\x00\x00\x00\x00WEBPimage"
        self.assert_content(self.upload(jpeg, "photo.jpg", mime="image/jpeg"), jpeg)
        self.assert_content(self.upload(webp, "photo.webp", mime="image/webp"), webp)
        # Exact limit remains accepted; one extra byte must fail before storage.
        maximum = PNG + b"x" * (10 * 1024 * 1024 - len(PNG))
        self.upload(maximum)
        for data, mime, status in ((b"", "image/png", 400), (b"bad", "image/png", 400),
                                   (PNG, "video/mp4", 415), (maximum + b"x", "image/png", 413)):
            with self.subTest(status=status), patch.object(self.storage, "put_bytes") as write:
                response = self.client.post("/v2/artifacts/uploads", files={"file": ("image.png", data, mime)})
                self.assertEqual(response.status_code, status)
                write.assert_not_called()

    def test_failed_storage_does_not_publish_artifact(self):
        with patch.object(self.storage, "put_bytes", side_effect=OSError("storage failed")):
            with self.assertRaises(OSError):
                self.client.post("/v2/artifacts/uploads", files={"file": ("photo.png", PNG, "image/png")})
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 0)

    def test_db_failure_leaves_unpublished_orphan_only(self):
        with patch("app.artifacts.service.create_artifact", side_effect=RuntimeError("database failed")):
            with self.assertRaises(RuntimeError):
                service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=PNG,
                    mime_type="image/png", filename="reference.png", origin="reference")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 0)
        self.assertEqual(len(list(self.storage.base_dir.iterdir())), 1)

    def test_outside_root_row_cannot_expose_content_or_locator(self):
        outside = self.storage.base_dir.parent / "private-secret.png"
        outside.write_bytes(PNG)
        with self.sessions() as db:
            artifact = service.create_artifact(db, tenant_id="tenant-one", user_id="user-one",
                media_type="image", storage_uri=outside.as_uri(), filename="photo.png")
            artifact_id = artifact.artifact_id
            db.commit()
        response = self.client.get(f"/v2/artifacts/{artifact_id}/content")
        self.assertEqual(response.status_code, 501)
        self.assertNotIn(str(outside), response.text)
        self.assertNotEqual(response.content, PNG)

    def test_legacy_artifact_row_remains_readable_and_keeps_identity(self):
        old = self.storage.base_dir / "old photo.png"
        old.parent.mkdir()
        old.write_bytes(PNG + b"old")
        with self.sessions() as db:
            artifact = service.create_artifact(db, tenant_id="tenant-one", user_id="user-one",
                media_type="image", mime_type="image/png", storage_uri=old.as_uri(), filename=old.name)
            artifact_id = artifact.artifact_id
            db.commit()
        self.upload(PNG + b"new", filename=old.name)
        self.assert_content(artifact_id, PNG + b"old")


class GeneratedImageStorageTests(contracts.ImageExecutionTestCase):
    def test_generated_and_uploaded_reference_bytes_survive_real_local_storage(self):
        storage = install_storage(self)
        self.enterContext(patch("app.images.tasks.artifact_store", storage))
        uploaded = service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=PNG,
            mime_type="image/png", filename="reference.png", origin="reference")
        response = self.submit_image(references=[{"artifact_id": uploaded.artifact_id}])
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job_id = db.scalar(select(Job.job_id))
        execute_job(job_id)
        self.assertEqual(self.provider.generate.call_args.args[0].references[0].data, PNG)
        with self.sessions() as db:
            generated = db.scalar(select(Artifact).where(Artifact.artifact_id != uploaded.artifact_id))
            generated_id = generated.artifact_id
            self.assertNotIn(generated.filename, generated.storage_uri)
        content = self.client.get(f"/v2/artifacts/{generated_id}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, self.provider.generate.return_value.data)
        # A generated ID can be used again as an input to the existing image flow.
        self.assertEqual(self.submit_image(references=[{"artifact_id": generated_id}]).status_code, 202)
        with self.sessions() as db:
            next_id = db.scalar(select(Job.job_id).where(Job.status == "queued"))
        execute_job(next_id)
        self.assertEqual(self.provider.generate.call_args.args[0].references[0].data, content.content)


class GeneratedVideoStorageTests(video.VideoTestCase):
    def test_video_staging_finalization_and_content_use_real_local_storage(self):
        install_storage(self)
        job_id, _, token = self.start_video()
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse()
        continue_job(job_id, token)
        with self.sessions() as db:
            artifact = db.scalar(select(Artifact))
            artifact_id = artifact.artifact_id
            self.assertEqual(db.scalar(select(Job.status).where(Job.job_id == job_id)), "completed")
            self.assertNotIn(artifact.filename, artifact.storage_uri)
        response = self.client.get(f"/v2/artifacts/{artifact_id}/content")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.content, MP4)
