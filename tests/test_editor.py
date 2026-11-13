"""Real authentication/DB/API policy boundaries with mocked generation providers."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from urllib.parse import urlsplit
from unittest.mock import patch

import jwt
from sqlalchemy import select
from tests.test_integration_client import ClientServerTests, CONTEXT, IMAGE, KEY
from app.core.time import utc_now
from app.db.models import EditorSession, ServiceCredential, TenantMembership
from app.jobs.worker import execute_job

PARENT = "https://sandcastle.example.test"


class EditorTests(ClientServerTests):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {"IG_EDITOR_BASE_URL": "https://testserver"}))
        with self.sessions() as db:
            db.get(ServiceCredential, "sandcastle-key-1").editor_origins = [PARENT, "http://localhost:5180"]
            db.commit()

    def launch(self, **patches):
        payload = {"correlation_id": "scene-1", "parent_origin": PARENT, "draft": deepcopy(IMAGE), **patches}
        return self.client.post("/v2/editor/sessions", json=payload,
            headers={"Authorization": "Bearer " + self.helper.credentials.token(CONTEXT, ["editor:launch"])})

    def redeem(self, launch=None):
        launch = launch or self.launch().json()
        token = urlsplit(launch["launch_url"]).fragment.removeprefix("launch=")
        return self.client.post(f"/v2/editor/sessions/{launch['session_id']}/redeem", json={"launch_token": token},
            headers={"Origin": "https://testserver"})

    def editor_headers(self, state=None):
        state = state or self.redeem().json()
        return {"X-IG-Editor-Session": state["session_id"], "X-IG-Editor-Token": state["editor_token"], "Origin": "https://testserver"}

    def submit_editor(self, headers, *, key="attempt_1", draft=None):
        return self.client.post("/v2/media/generations", headers={**headers, "Idempotency-Key": key}, json=draft or IMAGE)

    def test_valid_service_launch_and_no_service_credential_in_browser_response(self):
        response = self.launch(callback_context={"scene_id": "local-scene"})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["correlation_id"], "scene-1")
        self.assertNotIn("editor_token", data)
        self.assertNotIn("Bearer", response.text)
        state = self.redeem(data).json()
        self.assertEqual((state["draft"]["intent"]["description"], state["callback_context"]), ("A castle", {"scene_id": "local-scene"}))
        with self.sessions() as db:
            row = db.get(EditorSession, data["session_id"])
            self.assertIsNone(row.launch_hash)
            self.assertNotEqual(row.access_hash, state["editor_token"])

    def test_launch_authentication_scope_audience_actor_and_tenant(self):
        from ig_client import DelegatedContext
        for context, scope, status, code in (
            (CONTEXT, "jobs:read", 403, "INSUFFICIENT_SCOPE"),
            (DelegatedContext(CONTEXT.actor_issuer, "unknown", "tenant-one"), "editor:launch", 403, "ACTOR_NOT_AUTHORIZED"),
            (DelegatedContext(CONTEXT.actor_issuer, CONTEXT.actor_subject, "tenant-two"), "editor:launch", 403, "TENANT_NOT_AUTHORIZED")):
            token = self.helper.credentials.token(context, [scope])
            response = self.client.post("/v2/editor/sessions", json={"correlation_id": "scene", "parent_origin": PARENT, "draft": IMAGE}, headers={"Authorization": "Bearer " + token})
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (status, code))
        for token in ("invalid", jwt.encode({"aud": "wrong"}, KEY, algorithm="RS256", headers={"kid": "sandcastle-key-1", "typ": "ig-service+jwt"})):
            self.assertEqual(self.client.post("/v2/editor/sessions", json={"correlation_id": "x", "parent_origin": PARENT, "draft": IMAGE}, headers={"Authorization": "Bearer " + token}).status_code, 401)

    def test_actor_without_membership_cannot_launch(self):
        with self.sessions() as db:
            row = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == "tenant-one", TenantMembership.user_id == "user-one"))
            row.status = "inactive"
            db.commit()
        self.assertEqual(self.launch().status_code, 403)

    def test_origin_must_be_exact_and_registered(self):
        self.assertEqual(self.launch(parent_origin="http://localhost:5180").status_code, 201)
        for value in ("https://arbitrary.test", "https://sandcastle.example.test.evil.test"):
            self.assertEqual(self.launch(parent_origin=value).status_code, 403)
        for value in ("*", "sandcastle.example.test", "https://*.example.test", "https://test;script-src", "https://test/path", "https://test\n"):
            self.assertEqual(self.launch(parent_origin=value).status_code, 422, value)

    def test_csp_frame_ancestors_exact_and_normal_pages_protected(self):
        launch = self.launch().json()
        response = self.client.get(urlsplit(launch["launch_url"]).path)
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors " + PARENT, response.headers["content-security-policy"])
        self.assertNotIn("launch=", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", self.client.get("/health").headers["content-security-policy"])

    def test_launch_one_time_replay_expiry_and_lifetime(self):
        launch = self.launch().json()
        with self.sessions() as db:
            row = db.get(EditorSession, launch["session_id"])
            self.assertEqual(int((row.launch_expires_at - row.created_at).total_seconds()), 60)
            self.assertEqual(int((row.expires_at - row.created_at).total_seconds()), 1800)
        self.assertEqual(self.redeem(launch).status_code, 200)
        self.assertEqual(self.redeem(launch).status_code, 410)
        expired = self.launch().json()
        with self.sessions() as db:
            db.get(EditorSession, expired["session_id"]).launch_expires_at = utc_now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.redeem(expired).status_code, 410)

    def test_access_expiry_revocation_and_identity_binding(self):
        headers = self.editor_headers()
        response = self.client.get("/v2/editor/session", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("user_id", response.json())
        for changed in ({**headers, "X-IG-Editor-Session": "other"}, {**headers, "X-IG-Editor-Token": "bad"}, {**headers, "Origin": PARENT}):
            self.assertIn(self.client.get("/v2/editor/session", headers=changed).status_code, (401, 403))
        with self.sessions() as db:
            db.get(EditorSession, headers["X-IG-Editor-Session"]).expires_at = utc_now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get("/v2/editor/session", headers=headers).status_code, 401)

    def test_registry_revocation_and_origin_removal_revoke_editor(self):
        headers = self.editor_headers()
        with self.sessions() as db:
            db.get(ServiceCredential, "sandcastle-key-1").editor_origins = []
            db.commit()
        self.assertEqual(self.client.get("/v2/media/capabilities", headers=headers).status_code, 401)

    def test_browser_and_service_credentials_cannot_expand_editor_routes(self):
        headers = self.editor_headers()
        self.assertEqual(self.client.get("/v2/tenant", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/v2/executions", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/v2/editor/session", headers={**headers, "Authorization": "Bearer invalid"}).status_code, 401)
        self.assertEqual(self.client.post("/v2/artifacts/uploads", headers={"Authorization": "Bearer " + self.helper.credentials.token(CONTEXT, ["editor:launch"])}, files={"file": ("a.png", b"x", "image/png")}).status_code, 403)

    def test_draft_identity_fields_and_storage_urls_rejected(self):
        self.assertEqual(self.launch(draft={**IMAGE, "user_id": "other"}).status_code, 422)
        self.assertEqual(self.launch(draft={**IMAGE, "references": [{"url": "https://storage.test"}]}).status_code, 422)
        self.assertEqual(self.launch(draft={**IMAGE, "references": [{"artifact_id": "private", "role": "character"}]}).status_code, 400)

    def test_invalid_model_controls_preserved_with_errors(self):
        draft = {**IMAGE, "interface_level": "hard", "advanced": {"provider": "openai", "model": "gone-model"}, "output": {"aspect_ratio": "16:9"}}
        launch = self.launch(draft=draft)
        self.assertEqual(launch.status_code, 201)
        state = self.redeem(launch.json()).json()
        self.assertEqual(state["draft"], draft)
        self.assertTrue(state["draft_errors"])

    def test_submission_attempt_idempotency_and_correlation(self):
        headers = self.editor_headers()
        first = self.submit_editor(headers)
        self.assertEqual(first.status_code, 202, first.text)
        retry = self.submit_editor(headers)
        self.assertEqual(first.json()["job_id"], retry.json()["job_id"])
        conflict = self.submit_editor(headers, draft={**IMAGE, "intent": {"description": "revised"}})
        self.assertEqual(conflict.status_code, 409)
        new = self.submit_editor(headers, key="attempt_2", draft={**IMAGE, "intent": {"description": "revised"}})
        self.assertNotEqual(first.json()["job_id"], new.json()["job_id"])
        jobs = self.client.get("/v2/jobs", headers=headers).json()
        self.assertEqual(len(jobs), 2)
        self.assertEqual({item["editor_attempt_id"] for item in jobs}, {"attempt_1", "attempt_2"})

    def test_unconfirmed_delivery_keeps_atomic_editor_attempt_visible(self):
        headers = self.editor_headers()
        self.queue.enqueue.side_effect = RuntimeError("simulated lost acknowledgement")
        response = self.submit_editor(headers)
        self.assertEqual(response.status_code, 503)
        jobs = self.client.get("/v2/jobs", headers=headers).json()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["editor_attempt_id"], "attempt_1")
        self.queue.enqueue.side_effect = None
        retried = self.submit_editor(headers)
        self.assertEqual(retried.json()["job_id"], jobs[0]["job_id"])

    def test_python_helper_launch_response(self):
        from ig_client import EditorLaunchRequest
        session = self.helper.create_editor_session(CONTEXT, EditorLaunchRequest("scene-client", PARENT, IMAGE))
        self.assertEqual(session.correlation_id, "scene-client")
        self.assertNotIn("launch=", repr(session))

    def test_editor_jobs_are_isolated_between_launches(self):
        first_headers, other_headers = self.editor_headers(), self.editor_headers()
        job = self.submit_editor(first_headers).json()["job_id"]
        self.assertEqual(self.client.get("/v2/jobs", headers=other_headers).json(), [])
        self.assertEqual(self.client.get(f"/v2/jobs/{job}", headers=other_headers).status_code, 404)

    def test_media_operations_models_and_references_policy_enforced_server_side(self):
        for policy, request in (
            ({"allowed_media": ["image"]}, {"media_type": "video", "intent": {"description": "video"}}),
            ({"allowed_operations": ["image_to_video"]}, IMAGE),
            ({"model_switching": False}, {**IMAGE, "interface_level": "hard", "advanced": {"provider": "openai"}}),
            ({"reference_editing": False}, {**IMAGE, "references": [{"artifact_id": "new", "role": "character"}]})):
            headers = self.editor_headers(self.redeem(self.launch(policy=policy).json()).json())
            self.assertEqual(self.submit_editor(headers, draft=request).status_code, 403)

    def test_upload_policy_and_actor_owned_normal_upload(self):
        from tests.test_artifact_storage import PNG
        forbidden = self.editor_headers(self.redeem(self.launch(policy={"upload": False}).json()).json())
        self.assertEqual(self.client.post("/v2/artifacts/uploads", headers=forbidden, files={"file": ("ref.png", PNG, "image/png")}).status_code, 403)
        headers = self.editor_headers()
        uploaded = self.client.post("/v2/artifacts/uploads", headers=headers, files={"file": ("ref.png", PNG, "image/png")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        ref = {"artifact_id": uploaded.json()["artifact_id"], "role": "character", "strength": "normal"}
        state = self.redeem(self.launch(draft={**IMAGE, "references": [ref]}).json()).json()
        self.assertEqual(state["draft"]["references"], [ref])

    def test_close_does_not_cancel_paid_or_queued_work(self):
        headers = self.editor_headers()
        job = self.submit_editor(headers).json()["job_id"]
        self.assertEqual(self.client.post("/v2/editor/session/close", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/v2/jobs/{job}", headers=headers).status_code, 401)
        self.assertEqual(self.helper.get_job(CONTEXT, job).status, "queued")

    def test_explicit_cancel_and_result_identity_lookup(self):
        headers = self.editor_headers()
        job = self.submit_editor(headers).json()["job_id"]
        canceled = self.client.post(f"/v2/jobs/{job}/cancel", headers=headers)
        self.assertEqual(canceled.json()["status"], "canceled")
        other = self.submit_editor(headers, key="next").json()["job_id"]
        execute_job(other)
        executions = self.client.get(f"/v2/jobs/{other}/executions", headers=headers).json()
        output = executions[0]["metadata"]["artifact_id"]
        metadata = self.client.get(f"/v2/artifacts/{output}", headers=headers).json()
        self.assertEqual(metadata["generation"]["job_id"], other)
        self.assertNotIn("storage_uri", metadata)
