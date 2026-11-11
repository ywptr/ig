"""Artifact-library contracts over disposable storage/DB; no live providers."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO, StringIO
import json
import importlib.util
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from sqlalchemy import select, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError

from tests import test_media_job_contracts as contracts
from tests import test_artifact_storage as storage_tests
from tests import test_artifact_previews as preview_tests
from tests import test_service_access as access
from tests import test_video_generation as video_tests
from tests.video_support import operation_response, HTTPResponse, MP4, TEMP_VIDEO_URL
from app.jobs.continuation import continue_job
from app.artifacts import service
from app.artifacts.lineage import record_generation, inputs_for
from app.artifacts.media_metadata import inspect_media
from app.artifacts.references import reference_slots, resolve_references, validate_reference_shape
from app.db.models import Artifact, ArtifactGeneration, ArtifactInput, Execution, Job, UserIdentity
from app.jobs.worker import execute_job
from app.media.technical import ReferenceInputSlot, TechnicalCapabilities, ExecutionBehavior
from app.media.validation import MediaRequestValidationError


def png():
    output = BytesIO()
    Image.new("RGB", (120, 60), "blue").save(output, "PNG")
    return output.getvalue()


class ArtifactLibraryTests(contracts.ImageExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.storage = storage_tests.install_storage(self)
        self.enterContext(patch("app.images.tasks.artifact_store", self.storage))

    def artifact(self, *, tenant="tenant-one", user="user-one", media="image", name="blue.png", provenance="unknown", **values):
        with self.sessions() as db:
            row = service.create_artifact(db, tenant_id=tenant, user_id=user, media_type=media,
                filename=name, mime_type="image/png" if media == "image" else "video/mp4",
                storage_uri=self.storage.put_bytes(filename=name, data=png()), size_bytes=len(png()),
                provenance=provenance, **values)
            db.commit()
            return row.artifact_id

    def page(self, **params):
        response = self.client.get("/v2/artifacts/page", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_uploaded_provenance_and_image_metadata(self):
        response = self.client.post("/v2/artifacts/uploads", files={"file": ("blue.png", png(), "image/png")})
        self.assertEqual(response.status_code, 201)
        row = response.json()
        self.assertEqual(row["provenance"], "uploaded")
        self.assertEqual(row["filename"], "blue.png")
        self.assertEqual(row["media_properties"]["width"], 120)
        self.assertEqual(row["media_properties"]["height"], 60)
        self.assertEqual(row["sha256"], sha256(png()).hexdigest())
        self.assertEqual(row["metadata"]["ingest_method"], "upload")

    def test_generated_provenance_and_explicit_producer(self):
        response = self.submit_image()
        self.assertEqual(response.status_code, 202, response.text)
        job_id = self.queue.enqueue.call_args.args[0]
        execute_job(job_id)
        row = self.page()["items"][0]
        self.assertEqual(row["provenance"], "generated")
        self.assertEqual(row["generation"]["job_id"], job_id)
        with self.sessions() as db:
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
            self.assertEqual(row["generation"]["execution_id"], execution.execution_id)
            self.assertEqual(execution.metadata_json["artifact_id"], row["artifact_id"])

    def test_multiple_inputs_preserve_order_roles_and_strength(self):
        first, second = self.artifact(), self.artifact()
        response = self.submit_image(references=[{"artifact_id": first, "role": "character", "strength": "strong"},
            {"artifact_id": second, "role": "style", "strength": "loose"}])
        self.assertEqual(response.status_code, 202, response.text)
        execute_job(self.queue.enqueue.call_args.args[0])
        output = next(item for item in self.page()["items"] if item["provenance"] == "generated")
        detail = self.client.get(f"/v2/artifacts/{output['artifact_id']}").json()
        self.assertEqual([(item["artifact_id"], item["role"], item["strength"], item["position"]) for item in detail["inputs"]],
            [(first, "character", "strong", 0), (second, "style", "loose", 1)])
        self.assertIsNone(detail["parent_artifact_id"])

    def test_legacy_artifact_content_and_metadata_remain_readable(self):
        identity = self.artifact(metadata={"legacy_label": "kept"})
        row = self.client.get(f"/v2/artifacts/{identity}").json()
        self.assertEqual(row["provenance"], "unknown")
        self.assertIsNone(row["generation"])
        self.assertEqual(row["inputs"], [])
        self.assertEqual(row["metadata"], {"legacy_label": "kept"})
        self.assertEqual(self.client.get(f"/v2/artifacts/{identity}/content").content, png())

    def test_pagination_ties_no_duplicates_both_orders(self):
        identities = [self.artifact() for _ in range(7)]
        with self.sessions() as db:
            for row in db.scalars(select(Artifact)):
                row.created_at = datetime(2026, 1, 1)
            db.commit()
        for order in ("asc", "desc"):
            observed, cursor = [], None
            while True:
                page = self.page(page_size=2, order=order, **({"cursor": cursor} if cursor else {}))
                observed.extend(item["artifact_id"] for item in page["items"])
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break
            self.assertEqual(observed, sorted(identities, reverse=order == "desc"))

    def test_page_excludes_other_user(self):
        mine = self.artifact()
        self.artifact(user="user-two")
        self.assertEqual([item["artifact_id"] for item in self.page()["items"]], [mine])

    def test_page_excludes_other_tenant(self):
        mine = self.artifact()
        self.artifact(tenant="tenant-two")
        self.assertEqual([item["artifact_id"] for item in self.page()["items"]], [mine])

    def test_cursor_is_bound_to_owner_and_filters(self):
        self.artifact(); self.artifact()
        cursor = self.page(page_size=1)["next_cursor"]
        self.assertEqual(self.client.get("/v2/artifacts/page", params={"cursor": cursor, "q": "blue"}).status_code, 400)
        self.user_id = "user-two"
        self.assertEqual(self.client.get("/v2/artifacts/page", params={"cursor": cursor}).status_code, 400)

    def test_media_filter(self):
        self.artifact(); video = self.artifact(media="video")
        self.assertEqual([item["artifact_id"] for item in self.page(media_type="video")["items"]], [video])

    def test_provenance_filter(self):
        self.artifact(); uploaded = self.artifact(provenance="uploaded")
        self.assertEqual([item["artifact_id"] for item in self.page(provenance="uploaded")["items"]], [uploaded])

    def test_search_case_insensitive_and_literal_wildcards(self):
        self.artifact(name="OTHER.PNG")
        found = self.artifact(name="Blue_100%.PNG")
        for query in ("blue", "_100%"):
            self.assertEqual([item["artifact_id"] for item in self.page(q=query)["items"]], [found])

    def test_created_time_filter(self):
        identity = self.artifact()
        with self.sessions() as db:
            db.get(Artifact, identity).created_at = datetime(2026, 1, 1)
            db.commit()
        self.assertEqual(len(self.page(created_after="2026-01-01T00:00:00Z", created_before="2026-01-02T00:00:00Z")["items"]), 1)
        self.assertEqual(self.page(created_before="2026-01-01T00:00:00Z")["items"], [])

    def test_preview_metadata_no_storage_locator(self):
        identity = self.artifact()
        response = self.client.get("/v2/artifacts/page")
        row = response.json()["items"][0]
        self.assertEqual(row["preview"]["status"], "missing")
        self.assertIn(f"/v2/artifacts/{identity}/preview", row["preview"]["url"])
        self.assertNotIn("storage_uri", response.text)
        self.assertNotIn("file:", response.text)

    def test_bad_cursor_and_page_size_rejected(self):
        self.assertEqual(self.client.get("/v2/artifacts/page?cursor=garbage").status_code, 400)
        self.assertEqual(self.client.get("/v2/artifacts/page?page_size=101").status_code, 422)

    def test_reference_validation_accepts_current_slots(self):
        identity = self.artifact()
        for provider, model, media, role in (("openai", "gpt-image-2", "image", "style"),
                ("google", "gemini-3.1-flash-lite-image", "image", "character"),
                ("alibaba", "wan3.0-video", "video", "first_frame")):
            with self.sessions() as db:
                rows = resolve_references(db, [{"artifact_id": identity, "role": role}],
                    slots=reference_slots(provider, model, media), tenant_id="tenant-one", user_id="user-one",
                    store=self.storage, read_bytes=True)
                self.assertEqual(rows[0][2], png())

    def test_reference_invalid_mime_rejected_before_job(self):
        identity = self.artifact()
        with self.sessions() as db:
            db.get(Artifact, identity).mime_type = "image/gif"
            db.commit()
        response = self.submit_image(references=[{"artifact_id": identity}])
        self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()

    def test_reference_role_and_count_rejected(self):
        slots = reference_slots("alibaba", "wan3.0-video", "video")
        for references in ([{"role": "style"}], [{"role": "first_frame"}] * 2):
            with self.assertRaises(MediaRequestValidationError):
                validate_reference_shape(references, slots)
        self.assertEqual(validate_reference_shape([], slots), [])

    def test_reference_required_slot(self):
        slot = ReferenceInputSlot(name="frame", roles=("first_frame",), media_types=("image",),
            mime_types=("image/png",), min_count=1, max_count=1, max_bytes=100)
        with self.assertRaises(MediaRequestValidationError):
            validate_reference_shape([], (slot,))

    def test_reference_byte_bounds_actual_stream(self):
        identity = self.artifact()
        slot = ReferenceInputSlot(name="refs", roles=("style",), media_types=("image",),
            mime_types=("image/png",), max_count=1, max_bytes=10)
        with self.sessions() as db:
            db.get(Artifact, identity).size_bytes = 1
            db.commit()
            with self.assertRaises(MediaRequestValidationError):
                resolve_references(db, [{"artifact_id": identity, "role": "style"}], slots=(slot,),
                    tenant_id="tenant-one", user_id="user-one", store=self.storage, read_bytes=True)

    def test_cross_owner_reference_rejected_before_job(self):
        identity = self.artifact(user="user-two")
        self.assertEqual(self.submit_image(references=[{"artifact_id": identity}]).status_code, 400)

    def test_lineage_cannot_link_foreign_input_or_producer(self):
        output, foreign = self.artifact(), self.artifact(user="user-two")
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                record_generation(db, db.get(Artifact, output), inputs=[{"artifact_id": foreign}])
            db.rollback()
            with self.assertRaises(ValueError):
                record_generation(db, db.get(Artifact, output), inputs=[], job_id="missing", execution_id="missing")

    def test_reference_storage_failure_does_not_expose_locator(self):
        identity = self.artifact()
        with patch.object(self.storage, "iter_bytes", side_effect=ValueError("file:///private/root/secret")):
            with self.sessions() as db:
                with self.assertRaises(MediaRequestValidationError) as error:
                    resolve_references(db, [{"artifact_id": identity, "role": "style"}],
                        slots=reference_slots("openai", "gpt-image-2", "image"), tenant_id="tenant-one",
                        user_id="user-one", store=self.storage, read_bytes=True)
                self.assertNotIn("file:", str(error.exception))
                self.assertNotIn("secret", str(error.exception))

    def test_one_execution_can_produce_multiple_outputs(self):
        self.submit_image()
        job_id = self.queue.enqueue.call_args.args[0]
        execute_job(job_id)
        second = self.artifact(provenance="generated")
        with self.sessions() as db:
            producer = db.scalar(select(ArtifactGeneration))
            record_generation(db, db.get(Artifact, second), inputs=[], job_id=job_id, execution_id=producer.execution_id)
            db.commit()
        rows = self.page(provenance="generated")["items"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["generation"]["job_id"] for row in rows}, {job_id})
        self.assertEqual(len({row["generation"]["execution_id"] for row in rows}), 1)

    def test_video_metadata_real_container(self):
        self.storage.base_dir.mkdir(parents=True, exist_ok=True)
        data = preview_tests.encoded_video(self.storage.base_dir / "test.mp4", 3)
        values = inspect_media(data, "video")
        self.assertEqual((values["width"], values["height"]), (64, 48))
        self.assertAlmostEqual(values["duration_seconds"], 3)
        self.assertAlmostEqual(values["fps"], 2)
        self.assertEqual(values["sha256"], sha256(data).hexdigest())

    def test_malformed_media_metadata_remains_optional(self):
        for media in ("image", "video"):
            self.assertEqual(inspect_media(b"bad", media), {"sha256": sha256(b"bad").hexdigest()})

    def test_discovery_exposes_slots_consistent_with_legacy_controls(self):
        response = self.client.get("/v2/media/capabilities")
        self.assertEqual(response.status_code, 200)
        for media in response.json()["media_types"]:
            for provider in media["providers"]:
                for model in provider["models"]:
                    technical, controls = model["technical"], model["capabilities"]
                    if technical is None:
                        continue
                    slots = technical["reference_inputs"]
                    self.assertEqual(sum(slot["max_count"] for slot in slots), controls["max_references"])
                    self.assertEqual({role for slot in slots for role in slot["roles"]}, set(controls["reference_roles"]))
                    self.assertEqual({mime for slot in slots for mime in slot["mime_types"]}, set(technical["reference_mime_types"]))

    def test_worker_rechecks_reference_ownership_after_submission(self):
        identity = self.artifact()
        response = self.submit_image(references=[{"artifact_id": identity}])
        self.assertEqual(response.status_code, 202)
        with self.sessions() as db:
            db.get(Artifact, identity).user_id = "user-two"
            db.commit()
        with self.assertRaisesRegex(MediaRequestValidationError, "not accessible"):
            execute_job(self.queue.enqueue.call_args.args[0])
        self.provider.generate.assert_not_called()

    def test_typed_slot_future_audio_and_invalid_definitions(self):
        slot = ReferenceInputSlot(name="audio", roles=("reference",), media_types=("audio",),
            mime_types=("audio/wav",), max_count=2, max_bytes=1000)
        declaration = TechnicalCapabilities(input_media=("text", "audio"), output_media=("video",),
            operations=("text_to_video",), execution=ExecutionBehavior(mode="asynchronous"), reference_inputs=(slot,))
        self.assertEqual(TechnicalCapabilities.model_validate_json(declaration.model_dump_json()), declaration)
        with self.assertRaises(ValidationError):
            ReferenceInputSlot.model_validate({**slot.model_dump(), "min_count": 3})
        with self.assertRaises(ValidationError):
            TechnicalCapabilities.model_validate({**declaration.model_dump(), "reference_inputs": [slot.model_dump()] * 2})


class DelegatedArtifactLibraryTests(contracts.ImageExecutionTestCase):
    setUpClass = classmethod(access.ServiceAccessTests.setUpClass.__func__)
    register = access.ServiceAccessTests.register
    token = access.ServiceAccessTests.token
    headers = access.ServiceAccessTests.headers

    def test_service_page_enforces_delegated_ownership_and_scope(self):
        from app.auth.dependencies import get_current_user
        from app.main import app
        app.dependency_overrides.pop(get_current_user)
        storage_tests.install_storage(self)
        identities = []
        for tenant, user in (("tenant-one", "user-one"), ("tenant-one", "user-two"), ("tenant-two", "user-one")):
            row = service.create_uploaded_artifact(tenant_id=tenant, user_id=user, data=png(),
                mime_type="image/png", filename="same.png", origin="reference")
            identities.append(row.artifact_id)
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="identity-one", user_id="user-one", issuer=access.ISSUER, subject="human-one"))
            self.register(db)
            db.commit()
        response = self.client.get("/v2/artifacts/page", headers=self.headers(scope="artifacts:read"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["artifact_id"] for item in response.json()["items"]], identities[:1])
        self.assertNotIn("storage_uri", response.text)
        self.assertEqual(self.client.get("/v2/artifacts/page", headers=self.headers(scope="jobs:read")).status_code, 403)


class ArtifactLibraryMigrationTests(contracts.MediaJobTestCase):
    def migration(self):
        spec = importlib.util.spec_from_file_location("library_migration", Path(__file__).resolve().parents[1] /
            "alembic/versions/1a0003000003_artifact_library_foundations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_round_trip_preserves_legacy_data_and_backfills_only_evidence(self):
        # Start from the exact prior schema by downgrading the new additive fields.
        migration = self.migration()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            for identity, metadata in (("upload", '{"ingest_method":"upload","origin":"external_generation"}'),
                    ("output", '{}'), ("ambiguous", '{}'), ("unknown", '{}')):
                connection.execute(text("INSERT INTO artifacts (artifact_id, tenant_id, user_id, media_type, storage_uri, metadata_json, created_at) VALUES (:id, 'tenant-one', 'user-one', 'image', 'file:///legacy', :metadata, '2026-01-01')"),
                                   {"id": identity, "metadata": metadata})
            connection.execute(text("INSERT INTO jobs (job_id, tenant_id, user_id, capability, status, input_json, created_at) VALUES ('job', 'tenant-one', 'user-one', 'image.generate', 'completed', :inputs, '2026-01-01')"),
                {"inputs": '{"references":[{"artifact_id":"upload","role":"style","strength":"strong"},{"artifact_id":"unknown","role":"character"},"bad historical input"]}'})
            for identity, output in (("ex1", "output"), ("ex2", "ambiguous"), ("ex3", "ambiguous")):
                connection.execute(text("INSERT INTO executions (execution_id, job_id, tenant_id, user_id, status, attempt, metadata_json, created_at) VALUES (:id, 'job', 'tenant-one', 'user-one', 'completed', 1, :metadata, '2026-01-01')"),
                    {"id": identity, "metadata": '{"artifact_id":"' + output + '"}'})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            rows = dict(connection.execute(text("SELECT artifact_id, provenance FROM artifacts")).all())
            self.assertEqual(rows, {"upload": "uploaded", "output": "generated", "ambiguous": "unknown", "unknown": "unknown"})
            self.assertEqual(connection.execute(text("SELECT artifact_id, job_id, execution_id FROM artifact_generations")).all(), [("output", "job", "ex1")])
            self.assertEqual(connection.execute(text("SELECT input_artifact_id, position, role, strength FROM artifact_inputs ORDER BY position")).all(),
                [("upload", 0, "style", "strong"), ("unknown", 1, "character", None)])
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM artifacts")).scalar(), 4)
            self.assertIn("external_generation", json.dumps(connection.execute(text("SELECT metadata_json FROM artifacts WHERE artifact_id='upload'")).scalar()))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            self.assertEqual(dict(connection.execute(text("SELECT artifact_id, provenance FROM artifacts")).all()), rows)

    def test_postgresql_upgrade_and_downgrade_compile(self):
        sql = StringIO()
        context = MigrationContext.configure(url="postgresql://", opts={"as_sql": True, "output_buffer": sql})
        with Operations.context(context):
            self.migration().upgrade()
            self.migration().downgrade()
        self.assertIn("json_array_elements", sql.getvalue())
        self.assertIn("WITH ORDINALITY", sql.getvalue())
        self.assertIn("CREATE TABLE artifact_inputs", sql.getvalue())
        self.assertNotIn("DROP TABLE artifacts", sql.getvalue())


class VideoArtifactLibraryTests(video_tests.VideoTestCase):
    def test_video_finalization_records_producer_and_first_frame(self):
        reference = self.image_reference()
        job_id, execution_id, continuation_id = self.start_video(references=[{"artifact_id": reference, "role": "first_frame"}])
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse()
        continue_job(job_id, continuation_id)
        with self.sessions() as db:
            output = db.scalar(select(Artifact).where(Artifact.media_type == "video"))
            self.assertEqual(output.provenance, "generated")
            self.assertEqual(output.sha256, sha256(MP4).hexdigest())
            producer = db.get(ArtifactGeneration, output.artifact_id)
            self.assertEqual((producer.job_id, producer.execution_id), (job_id, execution_id))
            self.assertEqual(inputs_for(db, output), [{"artifact_id": reference, "position": 0,
                "relation": "reference", "role": "first_frame", "strength": None}])
