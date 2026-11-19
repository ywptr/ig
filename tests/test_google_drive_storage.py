"""User-owned Drive integration over isolated SQLite and fake Google APIs."""
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from unittest.mock import Mock, patch
import unittest
from cryptography.fernet import Fernet
from sqlalchemy import select, func
from tests import test_media_job_contracts as contracts
from tests import test_service_access as access
from tests.test_artifact_storage import local_store
from tests.test_artifact_previews import encoded_image
from app.db.models import User, TenantSettings, TenantMembership, DriveConnection, DriveOAuthState, Artifact, ArtifactPreview, Job
from app.artifacts.storage.router import RoutingArtifactStore
from app.artifacts.storage.google_drive import GoogleDriveArtifactStore, parse_locator
from app.artifacts.storage.selection import select_write_store
from app.storage_integrations import connections
from app.storage_integrations.google_drive import DriveClient, DriveError, SCOPE, API, UPLOAD
from app.security.secrets import decrypt_secret, encrypt_secret
from app.core.time import utc_now
from app.artifacts import service, previews
from app.artifacts.retention import policy, eligible, candidate_query
from app.jobs.worker import execute_job


class FakeDrive:
    def __init__(self, account):
        self.identity = account
        self.files = {}
        self.writes = []
        self.failure = None
        self.folder = "folder_" + account

    def account(self):
        return {"permissionId": self.identity, "emailAddress": self.identity + "@example.test", "displayName": self.identity}

    def create_folder(self):
        return self.folder

    def check_folder(self, folder):
        if self.failure:
            raise DriveError(self.failure)
        if folder != self.folder:
            raise DriveError("folder_unavailable")

    def upload(self, folder, filename, data):
        self.check_folder(folder)
        identity = "file_" + uuid4().hex
        self.files[identity] = bytes(data)
        self.writes.append((folder, filename, identity))
        return identity

    def stat(self, identity):
        if self.failure:
            raise DriveError(self.failure)
        if identity not in self.files:
            raise DriveError("not_found")
        return {"size": str(len(self.files[identity])), "trashed": False}

    def iter_bytes(self, identity, chunk_size):
        self.stat(identity)
        data = self.files[identity]
        yield from (data[n:n + chunk_size] for n in range(0, len(data), chunk_size))

    def delete(self, identity):
        self.files.pop(identity, None)


class DriveIntegrationTests(contracts.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {
            "TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(), "GOOGLE_DRIVE_CLIENT_ID": "drive-client",
            "GOOGLE_DRIVE_CLIENT_SECRET": "drive-client-secret", "GOOGLE_DRIVE_REDIRECT_URI": "http://testserver/v2/user/storage/google-drive/callback"}))
        # HTTPS is required outside localhost; use an HTTPS configured redirect, while TestClient uses HTTP.
        os.environ["GOOGLE_DRIVE_REDIRECT_URI"] = "https://ig.example.test/v2/user/storage/google-drive/callback"
        self.accounts = {"account_a": FakeDrive("account_a"), "account_b": FakeDrive("account_b")}
        def token(data):
            account = data.get("code") or data.get("refresh_token", "").removeprefix("refresh_")
            if account not in self.accounts:
                raise DriveError("revoked")
            return {"access_token": account, "refresh_token": "refresh_" + account, "scope": SCOPE}
        self.token = Mock(side_effect=token)
        for module in ("app.api.user_storage", "app.storage_integrations.connections"):
            self.enterContext(patch(module + ".token_request", self.token))
            self.enterContext(patch(module + ".DriveClient", side_effect=lambda token: self.accounts[token]))
        self.enterContext(patch("app.storage_integrations.connections.SessionLocal", self.sessions))
        self.local = local_store(self)
        self.router = RoutingArtifactStore(write_store=self.local, local_store=self.local, vercel_blob_store=self.local)
        self.router.register("gdrive", GoogleDriveArtifactStore(connections.resolve_connection, report_error=connections.report_error))
        for module in ("app.artifacts.service", "app.images.tasks", "app.artifacts.previews", "app.api.artifacts"):
            self.enterContext(patch(module + ".artifact_store", self.router))
        self.enterContext(patch("app.artifacts.service.SessionLocal", self.sessions))
        self.enterContext(patch("app.images.tasks.SessionLocal", self.sessions))
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.enterContext(patch("app.artifacts.retention.artifact_store", self.router))
        with self.sessions() as db:
            db.add(TenantSettings(tenant_id="tenant-one", google_drive_enabled=True))
            db.commit()
        self.png = encoded_image((64, 48))

    def connect(self, account="account_a"):
        response = self.client.post("/v2/user/storage/google-drive/connect")
        self.assertEqual(response.status_code, 200, response.text)
        params = parse_qs(urlparse(response.json()["authorization_url"]).query)
        self.assertEqual(params["scope"], [SCOPE])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        result = self.client.get("/v2/user/storage/google-drive/callback", params={"state": params["state"][0], "code": account}, follow_redirects=False)
        self.assertEqual(result.status_code, 303, result.text)
        import base64, hashlib
        verifier = self.token.call_args.args[0]["code_verifier"]
        self.assertEqual(base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(), params["code_challenge"][0])
        return params["state"][0]

    def prefer_drive(self):
        response = self.client.patch("/v2/user/storage", json={"artifact_storage_preference": "google_drive"})
        self.assertEqual(response.status_code, 200, response.text)

    def writer(self, user="user-one", original_uri=None):
        with self.sessions() as db:
            return select_write_store(db, tenant_id="tenant-one", user_id=user, managed_store=self.router, original_uri=original_uri)

    def test_independent_oauth_encrypted_and_private_owner_settings(self):
        state = self.connect()
        with self.sessions() as db:
            connection = connections.active_connection(db, "user-one")
            self.assertNotIn("refresh_account_a", connection.encrypted_refresh_token)
            self.assertEqual(decrypt_secret(connection.encrypted_refresh_token), "refresh_account_a")
            self.assertIsNone(db.get(User, "user-one").google_sub)
            self.assertEqual(db.scalar(select(func.count()).select_from(DriveOAuthState)), 0)
        body = self.client.get("/v2/user/storage").text
        for private in ("refresh_", "folder_account_a", "encrypted_refresh_token", "access_token", state):
            self.assertNotIn(private, body)
        self.user_id = "user-two"
        self.assertIsNone(self.client.get("/v2/user/storage").json()["connection"])
        self.assertEqual(self.client.get("/v2/user/storage").json()["artifact_storage_preference"], "ig_managed")
        self.assertEqual(self.client.get("/v2/user/storage/google-drive/callback", params={"state": state, "code": "account_a"}).status_code, 400)

    def test_wrong_user_session_cookie_expired_state_and_replay_rejected(self):
        response = self.client.post("/v2/user/storage/google-drive/connect")
        state = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
        self.user_id = "user-two"
        path = "/v2/user/storage/google-drive/callback"
        self.assertEqual(self.client.get(path, params={"state": state, "code": "account_a"}).status_code, 400)
        self.user_id = "user-one"
        self.assertEqual(self.client.get(path, params={"state": "forged", "code": "account_a"}).status_code, 400)
        self.client.cookies.set("ig_session", "changed")
        self.assertEqual(self.client.get(path, params={"state": state, "code": "account_a"}).status_code, 400)
        self.client.cookies.delete("ig_session")
        with self.sessions() as db:
            db.scalar(select(DriveOAuthState)).expires_at = utc_now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get(path, params={"state": state, "code": "account_a"}).status_code, 400)
        self.token.assert_not_called()
        self.connect()

    def test_two_accounts_route_owner_and_existing_reads_after_tenant_disable(self):
        self.connect(); self.prefer_drive()
        writer = self.writer()
        uri = writer.put_bytes(filename="a", data=b"A")
        self.assertEqual(b"".join(self.router.iter_bytes(uri)), b"A")
        self.assertTrue(self.router.exists(uri))
        self.assertTrue(self.writer("user-two").put_bytes(filename="b", data=b"B").startswith("file:"))
        self.user_id = "user-two"; self.connect("account_b"); self.prefer_drive()
        other = self.writer("user-two").put_bytes(filename="b", data=b"B")
        self.assertNotEqual(parse_locator(uri)[0], parse_locator(other)[0])
        with self.assertRaisesRegex(DriveError, "owner_mismatch"):
            self.writer("user-two", original_uri=uri)
        with self.sessions() as db:
            db.get(TenantSettings, "tenant-one").google_drive_enabled = False
            db.commit()
        with self.assertRaisesRegex(DriveError, "tenant_disabled"):
            writer.put_bytes(filename="late", data=b"late")
        self.assertEqual(b"".join(self.router.iter_bytes(uri)), b"A")
        self.assertEqual(b"".join(self.router.iter_bytes(other)), b"B")
        self.router.delete(uri); self.router.delete(uri)
        self.assertFalse(self.router.exists(uri))

    def test_disconnect_is_owner_only_and_no_fallback(self):
        self.connect(); self.prefer_drive()
        uri = self.writer().put_bytes(filename="a", data=b"A")
        self.user_id = "user-two"; self.connect("account_b"); self.prefer_drive()
        self.client.post("/v2/user/storage/google-drive/disconnect")
        self.assertEqual(b"".join(self.router.iter_bytes(uri)), b"A")
        self.assertEqual(self.client.get("/v2/user/storage").json()["artifact_storage_preference"], "google_drive")
        with self.assertRaises(DriveError):
            self.writer("user-two")
        response = self.client.patch("/v2/user/storage", json={"artifact_storage_preference": "ig_managed"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.writer("user-two").put_bytes(filename="b", data=b"B").startswith("file:"))

    def test_account_replacement_does_not_rebind_old_artifacts(self):
        self.connect(); self.prefer_drive()
        old = self.writer().put_bytes(filename="a", data=b"A")
        self.connect("account_b")
        new = self.writer().put_bytes(filename="b", data=b"B")
        self.assertNotEqual(parse_locator(old)[0], parse_locator(new)[0])
        with self.assertRaisesRegex(DriveError, "disconnected"):
            b"".join(self.router.iter_bytes(old))
        # Reconnect original account restores only its original connection/locator.
        self.connect("account_a")
        self.assertEqual(b"".join(self.router.iter_bytes(old)), b"A")

    def test_governance_admin_only_and_disabled_connection_denied(self):
        path = "/v2/tenant/settings/storage"
        self.assertEqual(self.client.patch(path, json={"google_drive_enabled": False}).status_code, 403)
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            db.commit()
        for enabled in (False, True, False):
            response = self.client.patch(path, json={"google_drive_enabled": enabled})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"google_drive_enabled": enabled})
        self.assertEqual(self.client.post("/v2/user/storage/google-drive/connect").status_code, 503)
        self.assertEqual(self.client.patch("/v2/user/storage", json={"artifact_storage_preference": "google_drive"}).status_code, 503)
        self.assertEqual(self.client.post("/v2/user/storage/google-drive/connect", headers={"Origin": "https://evil.test"}).status_code, 403)

    def test_upload_preview_same_connection_content_api_and_preference_change(self):
        self.connect(); self.prefer_drive()
        artifact = service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=self.png,
            mime_type="image/png", filename="same.png", origin="upload")
        with self.sessions() as db:
            row = db.get(Artifact, artifact.artifact_id)
            preview = db.scalar(select(ArtifactPreview).where(ArtifactPreview.artifact_id == row.artifact_id))
            self.assertEqual(row.provenance, "uploaded")
            self.assertEqual(preview.status, "ready")
            self.assertEqual(parse_locator(row.storage_uri)[0], parse_locator(preview.storage_uri)[0])
        content = self.client.get(f"/v2/artifacts/{artifact.artifact_id}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, self.png)
        result = self.client.get(f"/v2/artifacts/{artifact.artifact_id}/preview")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-type"], "image/webp")
        metadata = self.client.get(f"/v2/artifacts/{artifact.artifact_id}").text
        self.assertNotIn("gdrive:", metadata)
        self.assertNotIn(self.accounts["account_a"].writes[0][2], metadata)
        self.client.patch("/v2/user/storage", json={"artifact_storage_preference": "ig_managed"})
        self.assertIsInstance(self.writer(original_uri=artifact.storage_uri), GoogleDriveArtifactStore)
        self.user_id = "user-two"
        self.assertEqual(self.client.get(f"/v2/artifacts/{artifact.artifact_id}").status_code, 404)

    def test_unhealthy_storage_no_publication_and_explicit_health_recovery(self):
        self.connect(); self.prefer_drive()
        for failure in ("revoked", "permission_lost", "folder_unavailable", "unknown_error"):
            self.accounts["account_a"].failure = failure
            self.client.post("/v2/user/storage/google-drive/health")
            result = self.client.get("/v2/user/storage").json()
            self.assertEqual(result["connection"]["status"], failure)
            with self.assertRaises(DriveError):
                service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=self.png,
                    mime_type="image/png", filename="bad", origin="upload")
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 0)
        self.accounts["account_a"].failure = None
        self.assertEqual(self.client.post("/v2/user/storage/google-drive/health").json()["connection"]["status"], "healthy")

    def test_retention_backend_not_current_preference_and_external_override_separate(self):
        self.assertEqual(policy(None, "local")["generated_artifact_retention_days"], 30)
        self.assertEqual(policy(None, "vercel")["generated_artifact_retention_days"], 30)
        self.assertFalse(policy(None, "gdrive")["generated_artifact_retention_enabled"])
        local_uri = self.writer().put_bytes(filename="old", data=b"old")
        self.connect(); self.prefer_drive()
        drive_uri = self.writer().put_bytes(filename="new", data=b"new")
        with self.sessions() as db:
            settings = db.get(TenantSettings, "tenant-one")
            settings.generated_artifact_retention_enabled = True
            settings.generated_artifact_retention_days = 40
            original = service.create_artifact(db, tenant_id="tenant-one", user_id="user-one", media_type="image", storage_uri=local_uri, provenance="generated")
            drive = service.create_artifact(db, tenant_id="tenant-one", user_id="user-one", media_type="image", storage_uri=drive_uri, provenance="generated")
            for row in (original, drive): row.created_at = utc_now() - timedelta(days=41)
            db.commit()
            self.assertTrue(eligible(db, original, utc_now()))
            self.assertFalse(eligible(db, drive, utc_now()))
            self.assertEqual(set(db.scalars(candidate_query(db, utc_now()))), {original.artifact_id})
            self.assertEqual(policy(settings, "local")["generated_artifact_retention_days"], 40)
            settings.external_generated_retention_enabled = True
            settings.external_generated_retention_days = 50
            db.commit()
            self.assertFalse(eligible(db, drive, utc_now()))
            settings.external_generated_retention_days = 35
            db.commit()
            self.assertTrue(eligible(db, drive, utc_now()))
            self.assertEqual(set(db.scalars(candidate_query(db, utc_now()))), {original.artifact_id, drive.artifact_id})

    def test_browser_generation_and_failure_use_owner_preference(self):
        from app.tenancy.provider_configs import set_provider_config
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                credentials={"api_key": "test"}, config={"default_model": "gpt-image-2"})
        provider_result = contracts.ImageProviderResult(data=self.png, mime_type="image/png", provider="openai", model="gpt-image-2", size_bytes=len(self.png))
        with patch("app.images.tasks.generate_image", return_value=provider_result):
            self.connect(); self.prefer_drive()
            response = self.submit_image()
            self.assertEqual(response.status_code, 202, response.text)
            with self.sessions() as db: job_id = db.scalar(select(Job.job_id))
            execute_job(job_id)
            with self.sessions() as db:
                artifact = db.scalar(select(Artifact))
                self.assertEqual(artifact.user_id, "user-one")
                self.assertTrue(artifact.storage_uri.startswith("gdrive:"))
            # Queued work must recheck the tenant gate, without falling back.
            response = self.submit_image()
            with self.sessions() as db:
                pending = db.scalar(select(Job).where(Job.status == "queued"))
                pending_id = pending.job_id
                db.get(TenantSettings, "tenant-one").google_drive_enabled = False
                db.commit()
            with self.assertRaisesRegex(DriveError, "tenant_disabled"):
                execute_job(pending_id)
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(Job.status).where(Job.job_id == pending_id)), "failed")
                self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)


    def test_delegated_sandcastle_actor_and_idempotent_redelivery(self):
        from app.auth.dependencies import get_current_user
        from app.main import app
        from app.db.models import UserIdentity
        from app.tenancy.provider_configs import set_provider_config
        self.connect(); self.prefer_drive()
        self.user_id = "user-two"
        self.connect("account_b"); self.prefer_drive()
        access.ServiceAccessTests.setUpClass.__func__(type(self))
        for name in ("register", "token", "headers", "submit"):
            setattr(self, name, getattr(access.ServiceAccessTests, name).__get__(self, type(self)))
        with self.sessions() as db:
            db.add_all([UserIdentity(identity_id="identity-one", user_id="user-one", issuer=access.ISSUER, subject="human-one"),
                        UserIdentity(identity_id="identity-two", user_id="user-two", issuer=access.ISSUER, subject="human-two")])
            self.register(db)
            db.commit()
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                credentials={"api_key": "test"}, config={"default_model": "gpt-image-2"})
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        result = contracts.ImageProviderResult(data=self.png, mime_type="image/png", provider="openai", model="gpt-image-2", size_bytes=len(self.png))
        with patch("app.images.tasks.generate_image", return_value=result) as provider:
            first = self.submit(key="drive-a")
            self.assertEqual(first.status_code, 202, first.text)
            job_id = first.json()["job_id"]
            execute_job(job_id); execute_job(job_id)
            duplicate = self.submit(key="drive-a")
            self.assertEqual(duplicate.json()["job_id"], job_id)
            second = self.submit(key="drive-b", act={"iss": access.ISSUER, "sub": "human-two"}, user_id="user-one")
            self.assertEqual(second.status_code, 202, second.text)
            execute_job(second.json()["job_id"])
            self.assertEqual(provider.call_count, 2)
        with self.sessions() as db:
            rows = list(db.scalars(select(Artifact)))
            self.assertEqual(len(rows), 2)
            for row in rows:
                connection = db.get(DriveConnection, parse_locator(row.storage_uri)[0])
                self.assertEqual(row.user_id, connection.user_id)
            self.assertEqual({row.user_id for row in rows}, {"user-one", "user-two"})
        for account in self.accounts.values():
            self.assertEqual(len([item for item in account.writes if item[1] != "preview.webp"]), 1)
        self.assertEqual(self.client.get("/v2/user/storage", headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.post("/v2/user/storage/google-drive/connect", headers=self.headers()).status_code, 403)

    def test_video_staging_uses_claim_owner_and_drive_preview(self):
        from tests import test_video_generation as video
        from tests.video_support import operation_response, HTTPResponse, TEMP_VIDEO_URL, MP4
        from app.jobs.continuation import continue_job
        from app.db.models import Execution
        self.connect(); self.prefer_drive()
        video.VideoTestCase.configure(self)
        self.enterContext(patch("app.jobs.video_lifecycle.poll_interval", return_value=0))
        self.enterContext(patch("app.media.video_tasks.SessionLocal", self.sessions))
        http = self.enterContext(patch("ig_core.providers.alibaba.video.requests.request", return_value=operation_response("PENDING")))
        from tempfile import TemporaryDirectory
        from tests.test_artifact_previews import encoded_video
        directory = self.enterContext(TemporaryDirectory())
        real_video = encoded_video(Path(directory) / "video.mp4", 2)
        download = self.enterContext(patch("ig_core.providers.alibaba.video.requests.get", return_value=HTTPResponse(chunks=[real_video])))
        response = video.VideoTestCase.submit_video(self)
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        execute_job(job_id)
        with self.sessions() as db:
            token = db.scalar(select(Execution)).metadata_json["continuation"]["id"]
        http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        continue_job(job_id, token)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job.status).where(Job.job_id == job_id)), "completed")
            artifact = db.scalar(select(Artifact))
            preview = db.scalar(select(ArtifactPreview))
            self.assertEqual(artifact.user_id, "user-one")
            self.assertEqual(parse_locator(artifact.storage_uri)[0], parse_locator(preview.storage_uri)[0])
            identity = artifact.artifact_id
        self.assertEqual(self.client.get(f"/v2/artifacts/{identity}/content").content, real_video)
        download.assert_called_once()

    def test_disconnect_during_exchange_cancels_connection_intent(self):
        original = self.accounts["account_a"].create_folder
        def cancel():
            self.client.post("/v2/user/storage/google-drive/disconnect")
            return original()
        self.accounts["account_a"].create_folder = cancel
        response = self.client.post("/v2/user/storage/google-drive/connect")
        state = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
        result = self.client.get("/v2/user/storage/google-drive/callback", params={"state": state, "code": "account_a"})
        self.assertEqual(result.status_code, 400)
        with self.sessions() as db:
            self.assertIsNone(connections.active_connection(db, "user-one"))

    def test_external_retention_api_is_separate_and_admin_only(self):
        path = "/v2/tenant/settings/external-retention"
        self.assertEqual(self.client.get(path).status_code, 403)
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            db.commit()
        self.assertFalse(self.client.get(path).json()["generated_artifact_retention_enabled"])
        response = self.client.patch(path, json={"generated_artifact_retention_enabled": True, "generated_artifact_retention_days": 75})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generated_artifact_retention_days"], 75)
        self.assertEqual(self.client.get("/v2/tenant/settings/retention").json()["generated_artifact_retention_days"], 30)
        self.assertEqual(self.client.patch(path, json={"generated_artifact_retention_enabled": True, "generated_artifact_retention_days": 0}).status_code, 422)


    def test_status_reads_are_cached_and_oauth_logs_are_redacted(self):
        import logging
        from app.core.logging import configure_logging
        configure_logging()
        with self.assertLogs("httpx2", level="INFO") as captured:
            state = self.connect()
        messages = "\n".join(captured.output)
        self.assertNotIn(state, messages)
        self.assertNotIn("code=account_a", messages)
        self.assertNotIn("refresh_account_a", messages)
        self.token.reset_mock()
        for _ in range(2):
            response = self.client.get("/v2/user/storage")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.token.assert_not_called()

    def test_callback_cannot_be_used_without_browser_nonce_or_after_replay(self):
        response = self.client.post("/v2/user/storage/google-drive/connect")
        state = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
        self.client.cookies.clear()
        path = "/v2/user/storage/google-drive/callback"
        self.assertEqual(self.client.get(path, params={"state": state, "code": "account_a"}).status_code, 400)
        self.token.assert_not_called()
        state = self.connect()
        self.assertEqual(self.client.get(path, params={"state": state, "code": "account_a"}).status_code, 400)


    def test_forged_bound_writer_and_remote_write_failure_cannot_publish(self):
        self.connect(); self.prefer_drive()
        with self.sessions() as db:
            identity = connections.active_connection(db, "user-one").connection_id
        self.token.reset_mock()
        forged = GoogleDriveArtifactStore(connections.resolve_connection, connection_id=identity,
            user_id="user-two", tenant_id="tenant-one")
        with self.assertRaisesRegex(DriveError, "owner_mismatch"):
            forged.put_bytes(filename="forged", data=b"bad")
        self.token.assert_not_called()
        self.accounts["account_a"].failure = "folder_unavailable"
        with self.assertRaisesRegex(DriveError, "folder_unavailable"):
            service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=self.png,
                mime_type="image/png", filename="fails", origin="reference")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 0)
            self.assertEqual(connections.active_connection(db, "user-one").status, "folder_unavailable")


class DriveTransportTests(unittest.TestCase):
    def response(self, status=200, body=None, headers=None):
        result = Mock(status_code=status, headers=headers or {})
        result.json.return_value = body or {}
        return result

    def client(self, replies):
        request = self.enterContext(patch("ig_storage.backends.drive_client.requests.request", side_effect=replies))
        return DriveClient("private-access-token"), request

    def folder(self):
        return self.response(body={"id": "folder", "mimeType": "application/vnd.google-apps.folder",
            "trashed": False, "capabilities": {"canAddChildren": True}})

    def test_access_log_redaction_preserves_uvicorn_formatter_arguments(self):
        import logging
        from uvicorn.logging import AccessFormatter
        from app.core.logging import DriveOAuthLogFilter
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", "/v2/user/storage/google-drive/callback?code=private-code&state=private-state", "1.1", 303), None)
        DriveOAuthLogFilter().filter(record)
        result = AccessFormatter().format(record)
        self.assertNotIn("private-code", result)
        self.assertNotIn("private-state", result)
        self.assertEqual(len(record.args), 5)

    def test_chunk_resume_lost_ack_uses_one_file_id(self):
        import requests
        replies = [self.folder(), self.response(body={"ids": ["allocated"]}),
            self.response(headers={"Location": UPLOAD + "?upload_id=private"}),
            self.response(308, headers={"Range": "bytes=0-3"}),
            requests.ConnectionError("must not leak private-access-token"),
            self.response(200, {"id": "allocated"})]
        client, request = self.client(replies)
        with patch("ig_storage.backends.drive_client.CHUNK_BYTES", 4):
            self.assertEqual(client.upload("folder", "video.mp4", b"abcdefgh"), "allocated")
        calls = request.call_args_list
        self.assertEqual(len([c for c in calls if c.args[0] == "POST"]), 1)
        self.assertEqual(calls[2].kwargs["json"]["id"], "allocated")
        self.assertEqual(calls[2].kwargs["json"]["appProperties"],
                         {"ig_schema": "1", "ig_kind": "artifact"})
        self.assertEqual(calls[-1].kwargs["headers"]["Content-Range"], "bytes */8")
        self.assertEqual(calls[-2].kwargs["headers"]["Content-Range"], "bytes 4-7/8")

    def test_retry_failure_is_bounded_and_safe(self):
        import requests
        client, request = self.client([self.folder(), self.response(body={"ids": ["allocated"]}),
            self.response(headers={"Location": UPLOAD + "?upload_id=private"}),
            *[requests.ConnectionError("private-access-token") for _ in range(6)], self.response(404)])
        with self.assertRaisesRegex(DriveError, "upload_failed") as caught:
            client.upload("folder", "video", b"bytes")
        self.assertNotIn("private-access-token", str(caught.exception))
        self.assertEqual(request.call_count, 10)

    def test_resumable_location_cannot_redirect_credentials(self):
        for location in ("https://evil.test/upload", "http://www.googleapis.com/upload/drive/v3/files", "https://www.googleapis.com.evil.test/upload/drive/v3/files"):
            with self.subTest(location=location):
                client, request = self.client([self.folder(), self.response(body={"ids": ["allocated"]}), self.response(headers={"Location": location})])
                with self.assertRaisesRegex(DriveError, "invalid_upload_session"):
                    client.upload("folder", "video", b"bytes")
                self.assertEqual(request.call_count, 3)

    def test_stream_read_exists_stat_delete_and_safe_errors(self):
        stream = self.response()
        stream.iter_content.return_value = [b"a", b"b"]
        client, request = self.client([stream, self.response(204), self.response(404), self.response(401)])
        self.assertEqual(b"".join(client.iter_bytes("file", 1)), b"ab")
        stream.close.assert_called_once()
        client.delete("file"); client.delete("file")
        with self.assertRaisesRegex(DriveError, "revoked"):
            client.stat("file")
        for invalid in ("gdrive://other/file", "gdrive://" + str(uuid4()) + "/../secret", "gdrive://" + str(uuid4()) + "/file?token=private"):
            with self.assertRaises(DriveError): parse_locator(invalid)

    def test_folder_unavailable_and_revoked_refresh_safe(self):
        client, _ = self.client([self.response(404)])
        with self.assertRaisesRegex(DriveError, "folder_unavailable"):
            client.check_folder("folder")
        from app.storage_integrations.google_drive import token_request
        with patch.dict(os.environ, {"GOOGLE_DRIVE_CLIENT_ID": "id", "GOOGLE_DRIVE_CLIENT_SECRET": "secret", "GOOGLE_DRIVE_REDIRECT_URI": "https://ig.example.test/callback"}), patch("app.storage_integrations.google_drive.requests.post", return_value=self.response(400, {"error": "private-refresh"})):
            with self.assertRaisesRegex(DriveError, "revoked") as caught:
                token_request({"grant_type": "refresh_token", "refresh_token": "private-refresh"})
            self.assertNotIn("private-refresh", str(caught.exception))


class DriveMigrationTests(contracts.MediaJobTestCase):
    def test_additive_migration_round_trip_preserves_existing_data(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path("alembic/versions/1a0007000007_user_drive_storage.py")
        spec = importlib.util.spec_from_file_location("drive_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with self.sessions() as db:
            db.add(TenantSettings(tenant_id="tenant-one", generated_artifact_retention_enabled=False, generated_artifact_retention_days=77))
            db.commit()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
        with self.sessions() as db:
            self.assertEqual(db.get(User, "user-one").artifact_storage_preference, "ig_managed")
            settings = db.get(TenantSettings, "tenant-one")
            self.assertFalse(settings.google_drive_enabled)
            self.assertFalse(settings.generated_artifact_retention_enabled)
            self.assertEqual(settings.generated_artifact_retention_days, 77)
            self.assertIsNone(settings.external_generated_retention_enabled)
