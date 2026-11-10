"""Delegated requests use real auth, SQL persistence and workers; no provider I/O."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from tests import test_media_job_contracts as contracts
from tests import test_artifact_storage as storage_tests
from tests import test_video_generation as video_tests
from app.auth.dependencies import get_current_user
from app.auth.service_contract import AUDIENCE, TOKEN_TYPE, SCOPES
from app.auth.session import create_session
from app.db.models import Artifact, Execution, Job, MediaSubmissionKey, ServiceCredential, Tenant, TenantMembership, UserIdentity
from app.executions.service import create_execution
from app.jobs.worker import execute_job
from app.main import app
from app.ops.service_credentials import register_credential

ISSUER = "https://is.example.test"
ROOT = Path(__file__).resolve().parents[1]


class ServiceAccessTests(contracts.ImageExecutionTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        super().setUp()
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        self.storage = storage_tests.install_storage(self)
        self.enterContext(patch("app.images.tasks.artifact_store", self.storage))
        with self.sessions() as db:
            db.add_all([UserIdentity(identity_id="identity-one", user_id="user-one", issuer=ISSUER, subject="human-one"),
                        UserIdentity(identity_id="identity-two", user_id="user-two", issuer=ISSUER, subject="human-two")])
            self.register(db)
            db.commit()

    def register(self, db, key_id="sandcastle-key-1", service_id="sandcastle", **options):
        return register_credential(db, key_id=key_id, service_id=service_id, public_key=self.public_key,
            scopes=options.get("scopes", list(SCOPES)), tenant_ids=options.get("tenant_ids", ["tenant-one", "tenant-two"]),
            actor_issuers=options.get("actor_issuers", [ISSUER]))

    def token(self, *, kid="sandcastle-key-1", signing_key=None, headers=None, **claims):
        now = int(time.time())
        return jwt.encode({"iss": "sandcastle", "sub": "sandcastle", "aud": AUDIENCE,
            "iat": now, "exp": now + 300, "scope": " ".join(sorted(SCOPES)),
            "act": {"iss": ISSUER, "sub": "human-one"}, "tenant_id": "tenant-one", **claims},
            signing_key or self.private_key, algorithm="RS256", headers={"kid": kid, "typ": TOKEN_TYPE, **(headers or {})})

    def headers(self, **claims):
        return {"Authorization": "Bearer " + self.token(**claims)}

    def discover(self, **claims):
        return self.client.get("/v2/media/capabilities", headers=self.headers(**claims))

    def submit(self, key="execution-1", payload=None, **claims):
        return self.client.post("/v2/media/generations", headers={**self.headers(**claims), "Idempotency-Key": key},
            json=payload or {"media_type": "image", "intent": {"description": "A castle"}})

    def job_count(self):
        with self.sessions() as db:
            return db.scalar(select(func.count()).select_from(Job))

    def test_valid_service_request_without_hostname_domain(self):
        response = self.client.get("/v2/media/capabilities", headers={**self.headers(), "host": "service.invalid"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_missing_credential_requires_authentication(self):
        self.assertEqual(self.client.get("/v2/media/capabilities").status_code, 401)

    def test_invalid_signature(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertEqual(self.discover(signing_key=other).status_code, 401)

    def test_expired_token(self):
        self.assertEqual(self.discover(iat=int(time.time())-600, exp=int(time.time())-300).status_code, 401)

    def test_wrong_audience(self):
        for audience in ("another-api", [AUDIENCE, "another-api"]):
            with self.subTest(audience=audience):
                self.assertEqual(self.discover(aud=audience).status_code, 401)

    def test_revocation_is_effective_without_restart_or_token_expiry(self):
        headers = self.headers()
        self.assertEqual(self.client.get("/v2/media/capabilities", headers=headers).status_code, 200)
        with self.sessions() as db:
            db.get(ServiceCredential, "sandcastle-key-1").enabled = False
            db.commit()
        self.assertEqual(self.client.get("/v2/media/capabilities", headers=headers).status_code, 401)

    def test_missing_route_scope(self):
        response = self.discover(scope="jobs:read")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "INSUFFICIENT_SCOPE")

    def test_token_cannot_expand_registered_scopes(self):
        with self.sessions() as db:
            db.get(ServiceCredential, "sandcastle-key-1").scopes = ["jobs:read"]
            db.commit()
        self.assertEqual(self.discover().status_code, 401)

    def test_service_identity_algorithm_type_and_lifetime_are_bound(self):
        for claims in ({"iss": "other"}, {"sub": "other"}, {"kid": "unknown"},
                       {"headers": {"typ": "JWT"}}, {"exp": int(time.time())+301},
                       {"iat": int(time.time())+20}, {"scope": "tenant:admin"}):
            with self.subTest(claims=claims):
                self.assertEqual(self.discover(**claims).status_code, 401)
        unsigned = jwt.encode({"sub": "sandcastle"}, key="", algorithm="none")
        self.assertEqual(self.client.get("/v2/media/capabilities", headers={"Authorization": "Bearer "+unsigned}).status_code, 401)

    def test_malformed_credentials_return_generic_unauthorized(self):
        nested = jwt.utils.base64url_encode(b'{"nested":' * 1100 + b'0' + b'}' * 1100).decode()
        for authorization in ("Basic opaque", "Bearer not-a-token", "Bearer " + self.token(exp=float("inf")),
                              "Bearer " + nested + ".e30.c2ln"):
            response = self.client.get("/v2/media/capabilities", headers={"Authorization": authorization})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": {"code": "INVALID_SERVICE_CREDENTIAL"}})
        response = self.client.get("/v2/media/capabilities", headers=[
            ("Authorization", "Bearer " + self.token()), ("Authorization", "Bearer " + self.token())])
        self.assertEqual(response.status_code, 401)

    def test_service_cannot_access_admin_or_unscoped_mutations_even_as_admin(self):
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            db.commit()
        for method, path in (("GET", "/v2/tenant/providers"), ("GET", "/v2/tenant/members"),
                             ("POST", "/v2/jobs"), ("POST", "/v2/jobs/fake/cancel"),
                             ("POST", "/v2/artifacts/uploads")):
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path, headers=self.headers(),
                    json={"capability": "system.noop"}).status_code, 403)

    def test_unknown_actor_wrong_issuer_and_email_substitution_are_denied(self):
        for actor in ({"iss": ISSUER, "sub": "unknown"}, {"iss": "https://other.test", "sub": "human-one"},
                      {"iss": ISSUER, "sub": "one@example.test"}, {"email": "one@example.test"},
                      {"iss": ISSUER, "sub": "human-one", "user_id": "user-two"}):
            with self.subTest(actor=actor):
                self.assertEqual(self.discover(act=actor).status_code, 403)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(UserIdentity)), 2)

    def test_removed_identity_is_denied_without_jit_creation(self):
        with self.sessions() as db:
            db.delete(db.get(UserIdentity, "identity-one"))
            db.commit()
        self.assertEqual(self.discover().status_code, 403)

    def test_arbitrary_user_id_claim_is_never_used(self):
        response = self.submit(user_id="user-two")
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job.user_id)), "user-one")

    def test_tenant_must_be_registered_to_service_and_exist(self):
        self.assertEqual(self.discover(tenant_id="unknown-tenant").status_code, 403)
        with self.sessions() as db:
            db.get(ServiceCredential, "sandcastle-key-1").tenant_ids = ["tenant-one"]
            db.commit()
        self.assertEqual(self.discover(tenant_id="tenant-two").status_code, 403)

    def test_inactive_tenant_denied(self):
        with self.sessions() as db:
            db.get(Tenant, "tenant-one").status = "inactive"
            db.commit()
        self.assertEqual(self.discover().status_code, 403)

    def test_inactive_or_missing_actor_membership_denied(self):
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).status = "inactive"
            db.delete(db.get(TenantMembership, ("tenant-two", "user-one")))
            db.commit()
        self.assertEqual(self.discover().status_code, 403)
        self.assertEqual(self.discover(tenant_id="tenant-two").status_code, 403)

    def test_invalid_tenant_type_denied(self):
        self.assertEqual(self.discover(tenant_id=["tenant-one"]).status_code, 403)

    def test_host_and_arbitrary_actor_headers_cannot_override_signed_claims(self):
        response = self.client.get("/v2/media/capabilities", headers={**self.headers(), "host": "other.test",
            "X-IG-User-ID": "user-two", "X-IG-Tenant-ID": "tenant-two"})
        self.assertEqual(response.status_code, 200)
        # Only tenant one has OpenAI configured in the fixture.
        self.assertTrue(response.json()["media_types"][0]["providers"][0]["available"])

    def test_artifact_metadata_content_and_listing_enforce_actor_and_tenant(self):
        ids = []
        for tenant, user in (("tenant-one", "user-one"), ("tenant-one", "user-two"), ("tenant-two", "user-one")):
            artifact = storage_tests.service.create_uploaded_artifact(tenant_id=tenant, user_id=user,
                data=storage_tests.PNG, mime_type="image/png", filename="reference.png", origin="reference")
            ids.append(artifact.artifact_id)
        for index, artifact_id in enumerate(ids):
            for suffix in ("", "/content"):
                response = self.client.get(f"/v2/artifacts/{artifact_id}{suffix}", headers=self.headers())
                self.assertEqual(response.status_code, 200 if index == 0 else 404)
                self.assertNotIn("file:", response.text)
                self.assertNotIn("storage_uri", response.text)
        listing = self.client.get("/v2/artifacts", headers=self.headers())
        self.assertEqual([row["artifact_id"] for row in listing.json()], ids[:1])

    def test_jobs_and_executions_preserve_resource_isolation(self):
        good = self.submit().json()["job_id"]
        other = self.submit(key="other", act={"iss": ISSUER, "sub": "human-two"}).json()["job_id"]
        with self.sessions() as db:
            # Create a separate tenant's Job without selecting a provider.
            from app.jobs.service import create_job
            job = create_job(db, capability="system.noop", input_data={}, tenant_id="tenant-two",
                             user_id="user-one", defer_dispatch=True)
            foreign = job.job_id
            executions = [create_execution(db, job_id=j, tenant_id=t, user_id=u) for j,t,u in (
                (good,"tenant-one","user-one"), (other,"tenant-one","user-two"), (foreign,"tenant-two","user-one"))]
            execution_ids = [execution.execution_id for execution in executions]
            db.commit()
        for index, job_id in enumerate((good, other, foreign)):
            for path in (f"/v2/jobs/{job_id}", f"/v2/jobs/{job_id}/executions", f"/v2/executions/{execution_ids[index]}"):
                self.assertEqual(self.client.get(path, headers=self.headers()).status_code, 200 if index == 0 else 404)
        self.assertEqual(len(self.client.get("/v2/jobs", headers=self.headers()).json()), 1)
        self.assertEqual(len(self.client.get("/v2/executions", headers=self.headers()).json()), 1)

    def test_browser_cookie_and_hostname_tenancy_remain_unchanged(self):
        with self.sessions() as db:
            _, cookie = create_session(db, user_id="user-one")
        self.client.cookies.set("ig_session", cookie)
        first = self.client.get("/v2/media/capabilities")
        self.assertEqual(first.status_code, 200)
        other = self.client.get("/v2/media/capabilities", headers={"host": "other.test"})
        self.assertEqual(other.status_code, 200)
        self.assertFalse(other.json()["media_types"][0]["providers"][0]["available"])
        self.assertEqual(self.client.get("/v2/media/capabilities", headers={"host": "unknown.test"}).status_code, 404)
        self.assertEqual(self.client.post("/v2/media/generations", json={"media_type":"image", "intent":{"description":"browser"}}).status_code, 202)

    def test_mixed_authentication_rejected_even_for_invalid_cookie(self):
        for cookie in ("invalid", ""):
            self.client.cookies.set("ig_session", cookie)
            response = self.discover()
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"]["code"], "AMBIGUOUS_AUTHENTICATION")
        self.client.cookies.clear()

    def test_valid_browser_cookie_and_service_credential_are_ambiguous(self):
        with self.sessions() as db:
            _, cookie = create_session(db, user_id="user-one")
        self.client.cookies.set("ig_session", cookie)
        self.assertEqual(self.discover().status_code, 400)

    def test_first_and_duplicate_submission_return_same_job(self):
        first, duplicate = self.submit(), self.submit()
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json(), duplicate.json())
        self.assertEqual(self.job_count(), 1)
        self.queue.enqueue.assert_called_once_with(first.json()["job_id"])

    def test_duplicate_running_failed_canceled_and_completed_jobs_are_not_replaced(self):
        first = self.submit().json()
        for status in ("running", "failed", "canceled", "completed"):
            with self.sessions() as db:
                db.scalar(select(Job)).status = status
                db.commit()
            response = self.submit()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["job_id"], first["job_id"])
            self.assertEqual(response.json()["status"], status)
            self.assertEqual(self.job_count(), 1)
        self.queue.enqueue.assert_called_once()

    def test_duplicate_after_actual_worker_completion_and_redelivery_do_not_repeat_provider(self):
        first = self.submit().json()
        execute_job(first["job_id"])
        execute_job(first["job_id"])
        second = self.submit().json()
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(second["status"], "completed")
        self.provider.generate.assert_called_once()
        executions = self.client.get(f"/v2/jobs/{first['job_id']}/executions", headers=self.headers()).json()
        self.assertEqual(len(executions), 1)
        artifact_id = executions[0]["metadata"]["artifact_id"]
        self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/content", headers=self.headers()).status_code, 200)

    def test_changed_prompt_or_references_conflicts(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        for payload in ({"media_type":"image", "intent":{"description":"different"}},
                        {"media_type":"image", "intent":{"description":"A castle"}, "references":[{"artifact_id":"different"}]}):
            response = self.submit(payload=payload)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.job_count(), 1)

    def test_changed_execution_controls_are_fingerprint_material(self):
        self.submit()
        for fields in ({"interface_level":"medium"}, {"output":{"aspect_ratio":"1:1"}},
                       {"interface_level":"hard", "advanced":{"provider":"openai", "model":"gpt-image-2"}}):
            self.assertEqual(self.submit(payload={"media_type":"image", "intent":{"description":"A castle"}, **fields}).status_code, 409)

    def test_schema_defaults_canonicalize_equivalent_requests(self):
        first = self.submit().json()
        request = {"media_type":"image", "interface_level":"easy", "strategy":"single",
                   "intent":{"description":"A castle"}, "references":[], "advanced":None}
        self.assertEqual(self.submit(payload=request).json()["job_id"], first["job_id"])

    def test_same_key_different_actor_and_service_are_independent(self):
        first = self.submit().json()["job_id"]
        second = self.submit(act={"iss":ISSUER,"sub":"human-two"}).json()["job_id"]
        with self.sessions() as db:
            self.register(db, key_id="other-service-key", service_id="other-service")
            db.commit()
        third = self.submit(kid="other-service-key", iss="other-service", sub="other-service").json()["job_id"]
        self.assertEqual(len({first,second,third}), 3)

    def test_same_key_different_tenants_are_independent(self):
        with self.sessions() as db:
            from app.tenancy.provider_configs import set_provider_config
            set_provider_config(db, tenant_id="tenant-two", provider="openai", enabled=True,
                credentials={"api_key":"test-key"}, config={})
        first = self.submit().json()["job_id"]
        second = self.submit(tenant_id="tenant-two").json()["job_id"]
        self.assertNotEqual(first, second)

    def test_rotation_does_not_change_idempotency_scope(self):
        first = self.submit().json()["job_id"]
        with self.sessions() as db:
            self.register(db, key_id="sandcastle-key-2")
            db.get(ServiceCredential,"sandcastle-key-1").enabled=False
            db.commit()
        self.assertEqual(self.submit(kid="sandcastle-key-2").json()["job_id"], first)
        self.assertEqual(self.submit().status_code, 401)

    def test_response_loss_and_fresh_session_recover_existing_submission(self):
        first = self.submit().json()
        # No in-memory submission cache; close all request sessions and construct
        # a fresh HTTP client, as after an API process restart.
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            recovered = client.post("/v2/media/generations", headers={**self.headers(),"Idempotency-Key":"execution-1"},
                                   json={"media_type":"image","intent":{"description":"A castle"}})
        self.assertEqual(recovered.json()["job_id"], first["job_id"])
        self.assertEqual(self.job_count(),1)

    def test_enqueue_failure_keeps_durable_job_and_retry_republishes_same_identity(self):
        self.queue.enqueue.side_effect = RuntimeError("transport-secret-canary")
        response = self.submit()
        self.assertEqual(response.status_code,503)
        self.assertNotIn("transport-secret-canary",response.text)
        with self.sessions() as db:
            first = db.scalar(select(Job.job_id))
            self.assertIsNone(db.scalar(select(MediaSubmissionKey)).dispatched_at)
        self.queue.enqueue.side_effect = None
        response = self.submit()
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()["job_id"],first)
        self.assertEqual(self.job_count(),1)

    def test_queue_acceptance_then_lost_ack_republishes_only_same_job(self):
        with patch("app.media.submissions.utc_now",side_effect=RuntimeError("crash after send")):
            with self.assertRaises(RuntimeError):
                self.submit()
        response = self.submit()
        job_id = response.json()["job_id"]
        self.assertEqual(self.queue.enqueue.call_count,2)
        self.assertTrue(all(call.args==(job_id,) for call in self.queue.enqueue.call_args_list))
        execute_job(job_id)
        execute_job(job_id)
        self.provider.generate.assert_called_once()

    def test_failed_validation_rolls_back_submission_key_and_job(self):
        response = self.submit(payload={"media_type":"image","intent":{"description":"test"},"output":{"aspect_ratio":"9:16"}})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.job_count(),0)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(MediaSubmissionKey)),0)

    def test_missing_invalid_and_duplicate_idempotency_headers(self):
        body={"media_type":"image","intent":{"description":"test"}}
        self.assertEqual(self.client.post("/v2/media/generations",headers=self.headers(),json=body).status_code,400)
        for key in ("", "space not allowed", "x"*129):
            self.assertEqual(self.submit(key=key).status_code,400)
        headers=list(self.headers().items())+[("Idempotency-Key","one"),("Idempotency-Key","two")]
        self.assertEqual(self.client.post("/v2/media/generations",headers=headers,json=body).status_code,400)
        self.assertEqual(self.job_count(),0)

    def test_logs_use_internal_actor_and_job_ids_without_tokens_or_raw_actor(self):
        token=self.token()
        with self.assertLogs("app.auth.service_access",level="INFO") as logs:
            response=self.client.post("/v2/media/generations",headers={"Authorization":"Bearer "+token,
                "Idempotency-Key":"execution-1"},json={"media_type":"image","intent":{"description":"private-prompt-canary"}})
        output="\n".join(logs.output)
        for secret in (token,"Bearer ","human-one",ISSUER,"private-prompt-canary","execution-1"):
            self.assertNotIn(secret,output)
        for identifier in ("sandcastle","identity-one","tenant-one",response.json()["job_id"],"submission_id"):
            self.assertIn(identifier,output)

    def test_invalid_token_errors_and_logs_never_echo_credential(self):
        token=self.token(aud="bad-audience-secret")
        response=self.client.get("/v2/media/capabilities",headers={"Authorization":"Bearer "+token})
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.json(),{"detail":{"code":"INVALID_SERVICE_CREDENTIAL"}})
        self.assertNotIn(token,response.text)

    def test_registry_rejects_key_reassignment_and_private_keys(self):
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                self.register(db,service_id="another")
            with self.assertRaises(ValueError):
                register_credential(db,key_id="new",service_id="sandcastle",public_key=self.private_key.private_bytes(
                    serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),
                    scopes=list(SCOPES),tenant_ids=["tenant-one"],actor_issuers=[ISSUER])

    def test_offline_token_helper_needs_no_database_and_does_not_print_token(self):
        directory=self.storage.base_dir.parent
        private=directory/"private.pem"
        private.write_bytes(self.private_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        output=directory/"token.jwt"
        env={k:v for k,v in os.environ.items() if k!="DATABASE_URL"}
        command=[sys.executable,str(ROOT/"scripts/issue-service-token.py"),"--service-id","sandcastle",
            "--key-id","sandcastle-key-1","--actor-issuer",ISSUER,"--actor-subject","human-one",
            "--tenant-id","tenant-one","--private-key-file",str(private),"--output",str(output),"--scope","media:discover"]
        result=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,"")
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        token=output.read_text()
        self.assertEqual(self.client.get("/v2/media/capabilities",headers={"Authorization":"Bearer "+token}).status_code,200)
        again=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertNotEqual(again.returncode,0)
        self.assertNotIn(token,again.stderr)


class DelegatedVideoTests(video_tests.VideoTestCase):
    setUpClass = classmethod(ServiceAccessTests.setUpClass.__func__)
    register = ServiceAccessTests.register
    token = ServiceAccessTests.token
    headers = ServiceAccessTests.headers

    def setUp(self):
        super().setUp()
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="identity-one",user_id="user-one",issuer=ISSUER,subject="human-one"))
            self.register(db)
            db.commit()

    def test_service_video_retry_preserves_waiting_operation_without_resubmission(self):
        payload={"media_type":"video","intent":{"description":"A slow sunrise"}}
        headers={**self.headers(),"Idempotency-Key":"video-execution-1"}
        first=self.client.post("/v2/media/generations",headers=headers,json=payload)
        self.assertEqual(first.status_code,202,first.text)
        job_id=first.json()["job_id"]
        self.http.side_effect=None
        self.http.return_value=video_tests.operation_response()
        execute_job(job_id)
        retry=self.client.post("/v2/media/generations",headers=headers,json=payload)
        self.assertEqual(retry.json()["job_id"],job_id)
        self.assertEqual(retry.json()["status"],"waiting")
        self.assertEqual(self.http.call_count,1)
        self.assertEqual(sum(not call.kwargs for call in self.queue.enqueue.call_args_list), 1)
        self.assertEqual(first.json()["request_id"],job_id)


class ServiceSchemaTests(contracts.MediaJobTestCase):
    def test_additive_migration_round_trip_preserves_existing_tables(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            engine=create_engine(f"sqlite:///{Path(directory)/'migration.sqlite'}")
            self.addCleanup(engine.dispose)
            excluded={"service_credentials","media_submission_keys"}
            tables=[table for table in contracts.Base.metadata.sorted_tables if table.name not in excluded]
            contracts.Base.metadata.create_all(engine,tables=tables)
            spec=importlib.util.spec_from_file_location("service_migration",ROOT/"alembic/versions/1a0001000001_service_access_and_submission_keys.py")
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()
                    names=set(inspect(connection).get_table_names())
                    self.assertTrue(excluded <= names)
                    module.downgrade()
                    self.assertTrue({table.name for table in tables} <= set(inspect(connection).get_table_names()))

    def test_registry_cli_has_safe_development_setup_and_revocation(self):
        from sqlalchemy import create_engine
        from tempfile import TemporaryDirectory
        from sqlalchemy.orm import Session
        with TemporaryDirectory() as directory:
            engine=create_engine(f"sqlite:///{Path(directory)/'credentials.sqlite'}")
            self.addCleanup(engine.dispose)
            contracts.Base.metadata.create_all(engine)
            with Session(engine) as db:
                db.add(Tenant(tenant_id="tenant-dev",slug="dev",name="Dev"))
                db.commit()
            private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            public=Path(directory)/"public.pem"
            public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
            env={**os.environ,"DATABASE_URL":str(engine.url),"VERCEL_ENV":"development","VERCEL_TARGET_ENV":"development"}
            command=[sys.executable,str(ROOT/"scripts/configure-service-credential.py"),"--key-id","dev-key",
                "--service-id","sandcastle","--public-key-file",str(public),"--scope","media:discover",
                "--tenant-id","tenant-dev","--actor-issuer","http://localhost:8001"]
            result=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with Session(engine) as db:
                self.assertTrue(db.get(ServiceCredential,"dev-key").enabled)
            result=subprocess.run([sys.executable,str(ROOT/"scripts/configure-service-credential.py"),"--key-id","dev-key","--disable"],
                                  env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with Session(engine) as db:
                self.assertFalse(db.get(ServiceCredential,"dev-key").enabled)
            env["VERCEL_ENV"]="production"
            denied=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(denied.returncode,2)
