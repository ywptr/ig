"""Real deterministic codec processing over isolated files/SQLite; no provider calls."""
from datetime import timedelta
from io import BytesIO
import json
import random
from pathlib import Path
import signal
import struct
import subprocess
from unittest.mock import Mock, patch
import zlib

import av
from PIL import Image
from sqlalchemy import func, select

from tests import test_media_job_contracts as contracts
from tests import test_artifact_storage as storage_tests
from tests import test_service_access as access
from app.artifacts import previews, service
from app.artifacts.preview_policy import VERSION, frame_positions
from app.core.time import utc_now
from app.db.models import Artifact, ArtifactPreview


def encoded_image(size=(1600, 1000)):
    image = Image.frombytes("RGB", size, random.Random(11).randbytes(size[0] * size[1] * 3))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def encoded_video(path: Path, duration: int):
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=2)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.thread_count = 1
        for index in range(duration * 2):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 48), (index * 17 % 256, 60, 180)))
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path.read_bytes()


class ArtifactPreviewTests(contracts.MediaJobTestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = encoded_image()

    def setUp(self):
        super().setUp()
        self.storage = storage_tests.install_storage(self)
        self.enterContext(patch("app.artifacts.previews.artifact_store", self.storage))

    def original(self, data=None, media="image", mime="image/png", tenant="tenant-one", user="user-one"):
        data = self.png if data is None else data
        uri = self.storage.put_bytes(filename="original-media", data=data)
        with self.sessions() as db:
            artifact = service.create_artifact(db, tenant_id=tenant, user_id=user, media_type=media,
                mime_type=mime, storage_uri=uri, filename="display-name", size_bytes=len(data))
            db.commit()
            return artifact.artifact_id

    def preview(self, artifact_id, **options):
        return self.client.get(f"/v2/artifacts/{artifact_id}/preview", **options)

    def test_image_preview_smaller_preserves_aspect_and_original_bytes(self):
        artifact_id = self.original()
        response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        self.assertEqual(response.headers["content-type"], "image/webp")
        with Image.open(BytesIO(response.content)) as preview:
            self.assertEqual(preview.size, (512, 320))
            self.assertAlmostEqual(preview.width / preview.height, 1.6)
        self.assertLess(len(response.content), len(self.png) // 10)
        content = self.client.get(f"/v2/artifacts/{artifact_id}/content")
        self.assertEqual(content.content, self.png)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)
            self.assertEqual(db.get(Artifact, artifact_id).size_bytes, len(self.png))

    def test_same_source_derives_identical_bytes_across_artifact_ids(self):
        first, second = self.original(), self.original()
        preview_one, preview_two = self.preview(first), self.preview(second)
        self.assertEqual(preview_one.status_code, 200)
        self.assertEqual(preview_one.content, preview_two.content)
        self.assertEqual(preview_one.headers["etag"], preview_two.headers["etag"])

    def test_saved_previous_version_is_readable_after_policy_upgrade(self):
        artifact_id = self.original()
        first = self.preview(artifact_id)
        self.assertEqual(first.status_code, 200)
        with patch("app.api.artifacts.VERSION", 2), patch("app.artifacts.previews.VERSION", 2), patch(
            "app.artifacts.previews.process_source", side_effect=AssertionError("Never decode old version"),
        ):
            old = self.preview(artifact_id, params={"v": 1})
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.content, first.content)

    def test_original_locator_cannot_escape_local_storage_containment(self):
        outside = self.storage.base_dir.parent / "private-outside-image"
        outside.write_bytes(self.png)
        artifact_id = self.original()
        with self.sessions() as db:
            db.get(Artifact, artifact_id).storage_uri = outside.as_uri()
            db.commit()
        response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(str(outside), response.text)
        self.assertNotIn("file:", response.text)

    def test_legacy_history_exposes_artifact_link_without_changing_content(self):
        from app.db.models import ImageRequest
        artifact_id = self.original()
        with self.sessions() as db:
            db.add(ImageRequest(request_id="legacy-image", tenant_id="tenant-one", user_id="user-one",
                artifact_id=artifact_id, prompt="A lighthouse", status="completed"))
            db.commit()
        listing = self.client.get("/v1/images")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["artifact_id"], artifact_id)
        detail = self.client.get("/v1/images/legacy-image")
        self.assertEqual(detail.json()["artifact_id"], artifact_id)
        with patch("app.api.images.artifact_store", self.storage):
            content = self.client.get("/v1/images/legacy-image/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, self.png)

    def test_small_image_not_enlarged(self):
        artifact_id = self.original(encoded_image((80, 40)))
        response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.size, (80, 40))

    def test_orientation_applied_and_metadata_stripped(self):
        original = Image.new("RGB", (400, 200), "orange")
        exif = original.getexif()
        exif[274], exif[270] = 6, "private-metadata-canary"
        output = BytesIO()
        original.save(output, "JPEG", exif=exif)
        response = self.preview(self.original(output.getvalue(), mime="image/jpeg"))
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.size, (200, 400))
            self.assertFalse(image.getexif())
            self.assertNotIn("icc_profile", image.info)
        self.assertNotIn(b"private-metadata-canary", response.content)

    def test_repeated_requests_reuse_one_persisted_derivative(self):
        artifact_id = self.original()
        with patch("app.artifacts.previews.process_source", wraps=previews.process_source) as process:
            first, second = self.preview(artifact_id), self.preview(artifact_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, second.content)
        process.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 1)
        self.assertEqual(len(list(self.storage.base_dir.iterdir())), 2)

    def test_etag_revalidation_checks_auth_and_skips_storage_read(self):
        artifact_id = self.original()
        first = self.preview(artifact_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["cache-control"], "private, no-cache")
        self.assertEqual(first.headers["vary"], "Cookie, Authorization")
        with patch.object(self.storage, "iter_bytes", side_effect=AssertionError("304 must not read bytes")):
            second = self.preview(artifact_id, headers={"If-None-Match": "W/" + first.headers["etag"]})
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b"")
        self.user_id = "user-two"
        self.assertEqual(self.preview(artifact_id, headers={"If-None-Match": first.headers["etag"]}).status_code, 404)

    def test_cross_user_and_cross_tenant_requests_cannot_generate_or_read_preview(self):
        artifact_id = self.original()
        self.user_id = "user-two"
        self.assertEqual(self.preview(artifact_id).status_code, 404)
        self.user_id = "user-one"
        self.assertEqual(self.preview(artifact_id, headers={"host": "other.test"}).status_code, 404)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 0)

    def test_metadata_and_list_are_lightweight_and_never_expose_locators(self):
        artifact_id = self.original()
        with patch.object(self.storage, "iter_bytes", side_effect=AssertionError("Metadata must not read original")):
            metadata = self.client.get(f"/v2/artifacts/{artifact_id}").json()
            listing = self.client.get("/v2/artifacts").json()
        self.assertEqual(metadata["preview"]["status"], "missing")
        self.assertEqual(listing[0]["preview"]["url"], f"/v2/artifacts/{artifact_id}/preview?v=1")
        self.assertEqual(self.preview(artifact_id).status_code, 200)
        metadata = self.client.get(f"/v2/artifacts/{artifact_id}").json()
        self.assertEqual(metadata["preview"]["status"], "ready")
        self.assertEqual(metadata["preview"]["frame_count"], 1)
        self.assertNotIn("storage_uri", json.dumps(metadata))
        self.assertNotIn("file:", json.dumps(metadata))
        self.assertEqual(metadata["preview"]["version"], VERSION)

    def test_video_frame_counts_and_static_contact_sheets(self):
        for duration, expected in ((4, 1), (8, 3), (20, 5), (40, 6)):
            with self.subTest(duration=duration):
                source = encoded_video(self.storage.base_dir.parent / f"{duration}.mp4", duration)
                artifact_id = self.original(source, "video", "video/mp4")
                response = self.preview(artifact_id)
                self.assertEqual(response.status_code, 200, response.text[:300] if response.status_code != 200 else "")
                metadata = self.client.get(f"/v2/artifacts/{artifact_id}").json()["preview"]
                self.assertEqual(metadata["kind"], "contact_sheet")
                self.assertEqual(metadata["frame_count"], expected)
                self.assertEqual(metadata["positions"], frame_positions(duration))
                with Image.open(BytesIO(response.content)) as sheet:
                    self.assertEqual(sheet.width, min(expected, 3) * 256)
                    self.assertEqual(sheet.height, (1 if expected <= 3 else 2) * 144)
                self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/content").content, source)

    def test_deterministic_timeline_positions_and_maximum_count(self):
        self.assertEqual(frame_positions(4), [2])
        self.assertEqual(frame_positions(5), [0.5, 2.5, 4.5])
        self.assertEqual(frame_positions(15), [1.5, 7.5, 13.5])
        self.assertEqual(frame_positions(20), [2, 6, 10, 14, 18])
        self.assertEqual(frame_positions(600), [60, 156, 252, 348, 444, 540])
        self.assertEqual(len(frame_positions(10000)), 6)
        self.assertEqual(frame_positions(20), frame_positions(20))

    def test_malformed_image_is_failed_once_without_original_fallback(self):
        artifact_id = self.original(b"not an image")
        with patch("app.artifacts.previews.process_source", wraps=previews.process_source) as process:
            first, second = self.preview(artifact_id), self.preview(artifact_id)
        self.assertEqual(first.status_code, 422)
        self.assertEqual(second.status_code, 422)
        self.assertEqual(first.json()["status"], "failed")
        self.assertEqual(first.headers["cache-control"], "private, no-store")
        process.assert_called_once()
        self.assertNotIn("source.bin", first.text)
        self.assertNotIn("file:", first.text)

    def test_malformed_video_fails_safely(self):
        artifact_id = self.original(b"\x00\x00\x00\x18ftypisom-not-video", "video", "video/mp4")
        response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error_code"], "PROCESSING_FAILED")

    def test_oversized_decoded_dimensions_rejected(self):
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        source = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
        response = self.preview(self.original(source))
        self.assertEqual(response.status_code, 422)

    def test_source_byte_limit_is_enforced_before_processing(self):
        artifact_id = self.original(b"x" * 11)
        with patch("app.artifacts.previews.MAX_IMAGE_BYTES", 10), patch("app.artifacts.previews.subprocess.Popen") as process:
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error_code"], "SOURCE_SIZE_LIMIT")
        process.assert_not_called()

    def test_actual_stream_limit_does_not_trust_declared_size(self):
        artifact_id = self.original(b"x" * 11)
        with self.sessions() as db:
            db.get(Artifact, artifact_id).size_bytes = 1
            db.commit()
        with patch("app.artifacts.previews.MAX_IMAGE_BYTES", 10):
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error_code"], "SOURCE_SIZE_LIMIT")

    def test_process_timeout_kills_group_and_is_persisted_as_failure(self):
        artifact_id = self.original(b"video", "video", "video/mp4")
        process = Mock(pid=123456)
        process.communicate.side_effect = [subprocess.TimeoutExpired("decoder", 25), (b"", None)]
        with patch("app.artifacts.previews.subprocess.Popen", return_value=process) as launch, patch("app.artifacts.previews.os.killpg") as kill:
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error_code"], "PROCESSING_TIMEOUT")
        kill.assert_called_once_with(123456, signal.SIGKILL)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertNotIn("DATABASE_URL", launch.call_args.kwargs["env"])
        self.assertFalse(launch.call_args.kwargs.get("shell", False))
        self.assertEqual(self.preview(artifact_id).status_code, 422)

    def test_processing_and_capacity_states_do_not_duplicate_derivation(self):
        artifact_id = self.original()
        with self.sessions() as db:
            db.add(ArtifactPreview(artifact_id=artifact_id, kind="thumbnail", version=1, status="processing",
                claim_id="active", attempts=1, lease_expires_at=utc_now() + timedelta(seconds=60)))
            db.commit()
        with patch("app.artifacts.previews.process_source", side_effect=AssertionError("Active claim")):
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["retry-after"], "3")
        self.assertEqual(response.json()["status"], "processing")
        missing = self.original()
        slots = Mock()
        slots.acquire.return_value = False
        with patch("app.artifacts.previews.SLOTS", slots):
            self.assertEqual(self.preview(missing).status_code, 202)
        slots.release.assert_not_called()

    def test_expired_claim_recovered_but_attempts_are_bounded(self):
        artifact_id = self.original()
        with self.sessions() as db:
            db.add(ArtifactPreview(artifact_id=artifact_id, kind="thumbnail", version=1, status="processing",
                claim_id="expired", attempts=1, lease_expires_at=utc_now() - timedelta(seconds=10)))
            db.commit()
        self.assertEqual(self.preview(artifact_id).status_code, 200)
        with self.sessions() as db:
            row = db.get(ArtifactPreview, (artifact_id, "thumbnail", 1))
            self.assertEqual(row.attempts, 2)
            row.status = "processing"
            row.lease_expires_at = utc_now() - timedelta(seconds=10)
            db.commit()
        with patch("app.artifacts.previews.process_source", side_effect=AssertionError("Retry cap")):
            self.assertEqual(self.preview(artifact_id).status_code, 422)

    def test_stale_processor_cannot_publish_over_new_claim(self):
        artifact_id = self.original()
        real = previews.process_source
        def replaced(*args):
            result = real(*args)
            with self.sessions() as db:
                row = db.get(ArtifactPreview, (artifact_id, "thumbnail", 1))
                row.claim_id = "replacement"
                db.commit()
            return result
        with patch("app.artifacts.previews.process_source", side_effect=replaced):
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 202)
        with self.sessions() as db:
            self.assertIsNone(db.get(ArtifactPreview, (artifact_id, "thumbnail", 1)).storage_uri)

    def test_unsupported_media_and_version_are_explicit(self):
        artifact_id = self.original(b"text", "text", "text/plain")
        response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["status"], "unsupported")
        self.assertEqual(self.preview(self.original(), params={"v": 999}).status_code, 404)

    def test_storage_failure_is_cached_and_locators_are_private(self):
        artifact_id = self.original()
        with patch.object(self.storage, "put_bytes", side_effect=OSError("private-storage-uri-canary")):
            response = self.preview(artifact_id)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("canary", response.text)
        self.assertEqual(self.preview(artifact_id).status_code, 422)

    def test_migration_round_trip_is_additive(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy.schema import CreateTable
        from sqlalchemy.dialects import postgresql
        artifact_id = self.original()
        ArtifactPreview.__table__.drop(self.engine)
        spec = importlib.util.spec_from_file_location("preview_migration", Path(__file__).resolve().parents[1] / "alembic/versions/1a0009000009_artifact_previews.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
        with self.sessions() as db:
            self.assertIsNotNone(db.get(Artifact, artifact_id))
        compiled = str(CreateTable(ArtifactPreview.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn("PRIMARY KEY (artifact_id, kind, version)", compiled)
        from io import StringIO
        sql = StringIO()
        context = MigrationContext.configure(url="postgresql://", opts={"as_sql": True, "output_buffer": sql})
        with Operations.context(context):
            migration.upgrade()
        # Match UTCDateTime's existing UTC-naive DB representation rather than
        # relying on a PostgreSQL session timezone when binding lease timestamps.
        self.assertIn("TIMESTAMP WITHOUT TIME ZONE", sql.getvalue())



class DelegatedPreviewTests(contracts.ImageExecutionTestCase):
    setUpClass = classmethod(access.ServiceAccessTests.setUpClass.__func__)
    register = access.ServiceAccessTests.register
    token = access.ServiceAccessTests.token
    headers = access.ServiceAccessTests.headers

    def test_service_scope_reads_preview_under_original_ownership(self):
        from app.auth.dependencies import get_current_user
        from app.db.models import UserIdentity
        from app.main import app
        app.dependency_overrides.pop(get_current_user)
        storage = storage_tests.install_storage(self)
        self.enterContext(patch("app.artifacts.previews.artifact_store", storage))
        artifact = service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one",
            data=encoded_image((80, 40)), mime_type="image/png", filename="image.png", origin="reference")
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="identity-one", user_id="user-one", issuer=access.ISSUER, subject="human-one"))
            self.register(db)
            db.commit()
        path = f"/v2/artifacts/{artifact.artifact_id}/preview"
        self.assertEqual(self.client.get(path, headers=self.headers(scope="artifacts:read")).status_code, 200)
        self.assertEqual(self.client.get(path, headers=self.headers(scope="jobs:read")).status_code, 403)
