"""Standalone helper and real IG service-contract tests; no paid provider I/O."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT_ROOT = Path(__file__).resolve().parents[1] / "integrations/python"
sys.path.insert(0, str(CLIENT_ROOT))
from ig_client import (DelegatedContext, EditorLaunchRequest, IGClient, IGGenerationError,
    IGHTTPError, IGProtocolError, IGTransportError, IGWaitCancelled, IGWaitTimeout,
    ServiceCredentials, create_service_token)
from ig_client import contract
from ig_client.transport import HTTPResponse, RequestsTransport

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CONTEXT = DelegatedContext("https://is.example.test", "human-one", "tenant-one")
IMAGE = {"media_type": "image", "intent": {"description": "A castle"}}
VIDEO = {"media_type": "video", "intent": {"description": "A slow sunrise"}}


def response(data, status=200, headers=None):
    return HTTPResponse(status, headers or {"Content-Type": "application/json"}, json.dumps(data).encode())


def submitted(media="image"):
    return response({"job_id": "job", "request_id": "request", "status": "queued", "media_type": media}, 202)


def artifact(identifier="artifact", media="image"):
    return response({"artifact_id": identifier, "media_type": media, "mime_type": "video/mp4" if media == "video" else "image/png",
        "filename": "result.mp4" if media == "video" else "result.png", "size_bytes": 10,
        "generation": {"job_id": "job", "execution_id": "execution"}, "preview": {"status": "ready", "url": "https://private.invalid/secret"},
        "metadata": {"storage_uri": "private-secret"}, "storage_uri": "private-secret", "media_properties": {"width": 10}})


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.credentials = ServiceCredentials("sandcastle", "sandcastle-key-1", lambda: KEY)
        self.client = IGClient("https://ig.example.test", self.credentials, transport=self.transport,
                               retry_interval=0.001)

    def token(self, **overrides):
        return create_service_token(service_id="sandcastle", key_id="sandcastle-key-1", private_key=KEY,
            context=CONTEXT, scopes=["media:discover", "jobs:read"], **overrides)

    def test_wire_constants_match_existing_server_contract(self):
        from app.auth import service_contract
        for name in ("AUDIENCE", "TOKEN_TYPE", "MAX_TOKEN_SECONDS", "SCOPES"):
            self.assertEqual(getattr(contract, name), getattr(service_contract, name))

    def test_valid_token_all_claims_and_header(self):
        token = self.token()
        claims = jwt.decode(token, KEY.public_key(), algorithms=["RS256"], audience="ig-api", issuer="sandcastle")
        self.assertEqual(claims["sub"], "sandcastle")
        self.assertEqual(claims["act"], {"iss": CONTEXT.actor_issuer, "sub": CONTEXT.actor_subject})
        self.assertEqual(claims["tenant_id"], "tenant-one")
        self.assertEqual(set(claims["scope"].split()), {"media:discover", "jobs:read"})
        self.assertEqual(claims["exp"] - claims["iat"], 300)
        self.assertEqual(jwt.get_unverified_header(token), {"alg": "RS256", "kid": "sandcastle-key-1", "typ": "ig-service+jwt"})

    def test_explicit_audience_lifetime_and_rotation(self):
        token = self.token(audience="configured-api", expiry_seconds=60)
        claims = jwt.decode(token, KEY.public_key(), algorithms=["RS256"], audience="configured-api")
        self.assertEqual(claims["exp"] - claims["iat"], 60)
        rotated = ServiceCredentials("sandcastle", "rotation-2", lambda: KEY).token(CONTEXT, ["jobs:read"])
        self.assertEqual(jwt.get_unverified_header(rotated)["kid"], "rotation-2")

    def test_invalid_lifetimes_and_scope_rejected(self):
        for ttl in (0, -1, 301, True, 1.5):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.token(expiry_seconds=ttl)
        for scopes in ([], ["admin"]):
            with self.assertRaises(ValueError):
                self.credentials.token(CONTEXT, scopes)

    def test_small_rsa_key_is_rejected(self):
        with self.assertRaises(ValueError):
            create_service_token(service_id="sandcastle", key_id="key",
                private_key=rsa.generate_private_key(public_exponent=65537, key_size=1024),
                context=CONTEXT, scopes=["jobs:read"])

    def test_pem_private_key_source(self):
        from cryptography.hazmat.primitives import serialization
        pem = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        credentials = ServiceCredentials("sandcastle", "key", lambda: pem)
        claims = jwt.decode(credentials.token(CONTEXT, ["jobs:read"]), KEY.public_key(), algorithms=["RS256"], audience="ig-api")
        self.assertEqual(claims["tenant_id"], "tenant-one")

    def test_private_key_never_logged_or_in_exception_or_repr(self):
        with patch("logging.Logger._log") as log:
            self.token()
            with self.assertRaises(ValueError) as caught:
                create_service_token(service_id="sandcastle", key_id="key", private_key="secret-invalid-key",
                    context=CONTEXT, scopes=["jobs:read"])
        log.assert_not_called()
        self.assertNotIn("secret-invalid-key", str(caught.exception))
        self.assertNotIn("private_key_source", repr(self.credentials))

    def test_no_email_or_local_user_context_fields(self):
        with self.assertRaises(TypeError):
            DelegatedContext(email="person@example.test", user_id="user", tenant_id="tenant")
        with self.assertRaises(ValueError):
            self.client.submit_generation(CONTEXT, correlation_id="x", request={**IMAGE, "user_id": "other"})

    def test_origin_restrictions(self):
        for origin in ("http://ig.example.test", "https://user:password@ig.test", "https://ig.test/v2", "https://ig.test?token=x"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                IGClient(origin, self.credentials)
        IGClient("http://127.0.0.1:8000", self.credentials)

    def test_image_video_submission_and_references(self):
        for media, role in (("image", "character"), ("video", "first_frame")):
            self.transport.request.return_value = submitted(media)
            reference = {"artifact_id": "owned-artifact", "role": role}
            if media == "image": reference["strength"] = "normal"
            request = {"media_type": media, "intent": {"description": "brief"}, "references": [reference]}
            result = self.client.submit_generation(CONTEXT, correlation_id="scene:stable-1", request=request)
            self.assertEqual(result.media_type, media)
            call = self.transport.request.call_args
            self.assertEqual(json.loads(call.kwargs["body"]), request)
            self.assertEqual(call.kwargs["headers"]["Idempotency-Key"], "scene:stable-1")

    def test_requires_explicit_valid_key(self):
        for key in ("", "a b", "x" * 129, None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.client.submit_generation(CONTEXT, correlation_id=key, request=IMAGE)
        self.transport.request.assert_not_called()

    def test_references_reject_storage_urls_and_ui_syntax_fields(self):
        for ref in ({"url": "https://storage.test"}, {"artifact_id": "a", "storage_uri": "secret"}, {"prompt": "@artifact"}):
            with self.assertRaises(ValueError):
                self.client.submit_generation(CONTEXT, correlation_id="x", request={**IMAGE, "references": [ref]})

    def test_transport_and_503_retries_reuse_exact_body_and_key(self):
        self.transport.request.side_effect = [IGTransportError("lost response"),
            response({"detail": {"code": "SUBMISSION_DISPATCH_UNCONFIRMED"}}, 503), submitted()]
        result = self.client.submit_generation(CONTEXT, correlation_id="stable", request=IMAGE)
        self.assertEqual(result.job_id, "job")
        calls = self.transport.request.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(len({call.kwargs["body"] for call in calls}), 1)
        self.assertEqual({call.kwargs["headers"]["Idempotency-Key"] for call in calls}, {"stable"})

    def test_http_error_code_status_and_detail_preserved(self):
        cases = [(401, "INVALID_SERVICE_CREDENTIAL"), (403, "INSUFFICIENT_SCOPE"),
                 (403, "SERVICE_ROUTE_FORBIDDEN"), (403, "ACTOR_NOT_AUTHORIZED"),
                 (403, "TENANT_NOT_AUTHORIZED"), (400, "INVALID_IDEMPOTENCY_KEY"),
                 (409, "IDEMPOTENCY_CONFLICT"), (404, None)]
        for status, code in cases:
            detail = {"code": code} if code else "Resource not found"
            self.transport.reset_mock()
            self.transport.request.return_value = response({"detail": detail}, status)
            with self.subTest(code=code), self.assertRaises(IGHTTPError) as caught:
                self.client.get_job(CONTEXT, "job")
            error = caught.exception
            self.assertEqual((error.status, error.code, error.detail), (status, code, detail))
            self.assertFalse(error.retryable)
            self.transport.request.assert_called_once()

    def test_transport_exhaustion_is_distinct_and_bounded(self):
        self.transport.request.side_effect = IGTransportError("unavailable")
        with self.assertRaises(IGTransportError):
            self.client.get_job(CONTEXT, "job")
        self.assertEqual(self.transport.request.call_count, 3)

    def test_polling_all_active_states_to_completion(self):
        self.transport.request.side_effect = [response({"job_id": "job", "status": state}) for state in
            ("queued", "running", "waiting", "polling", "cancel_requested", "completed")]
        job = self.client.wait_for_job(CONTEXT, "job", timeout=2, poll_interval=0.001)
        self.assertEqual(job.status, "completed")
        self.assertTrue(all(call.args[0] == "GET" for call in self.transport.request.call_args_list))

    def test_terminal_failures_and_canceled_are_returned_without_repoll(self):
        for state, code in (("canceled", None), ("failed", "VIDEO_PENDING_TIMEOUT"),
            ("failed", "VIDEO_SUBMISSION_UNCERTAIN"), ("failed", "VIDEO_CHECKPOINT_INVALID"),
            ("failed", "VIDEO_PREPARATION_RETRIES_EXHAUSTED"), ("failed", "VIDEO_PROVIDER_FAILED")):
            self.transport.reset_mock()
            self.transport.request.return_value = response({"job_id": "job", "status": state, "error_code": code})
            job = self.client.wait_for_job(CONTEXT, "job", timeout=1)
            self.assertEqual((job.status, job.error_code), (state, code))
            self.transport.request.assert_called_once()

    def test_wait_timeout_preserves_identity_and_last_state(self):
        self.transport.request.return_value = response({"job_id": "job", "status": "waiting"})
        with self.assertRaises(IGWaitTimeout) as caught:
            self.client.wait_for_job(CONTEXT, "job", timeout=0.02, poll_interval=0.01)
        self.assertEqual(caught.exception.job_id, "job")
        self.assertEqual(caught.exception.last_job.status, "waiting")

    def test_local_cancel_does_not_call_remote_cancel(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(IGWaitCancelled) as caught:
            self.client.wait_for_job(CONTEXT, "job", timeout=1, cancel=cancel)
        self.assertEqual(caught.exception.job_id, "job")
        self.transport.request.assert_not_called()

    def test_cancel_after_read_interrupts_wait(self):
        cancel = Event()
        def pending(*args, **kwargs):
            cancel.set()
            return response({"job_id": "job", "status": "polling"})
        self.transport.request.side_effect = pending
        with self.assertRaises(IGWaitCancelled) as caught:
            self.client.wait_for_job(CONTEXT, "job", timeout=2, poll_interval=1, cancel=cancel)
        self.assertEqual(caught.exception.job_id, "job")
        self.transport.request.assert_called_once()

    def test_polling_retries_read_failures_without_submission(self):
        self.transport.request.side_effect = [IGTransportError("transient"), response({"job_id": "job", "status": "completed"})]
        self.assertEqual(self.client.wait_for_job(CONTEXT, "job", timeout=1).status, "completed")
        self.assertEqual({call.args[0] for call in self.transport.request.call_args_list}, {"GET"})

    def test_unknown_state_fails_closed(self):
        self.transport.request.return_value = response({"job_id": "job", "status": "new-state"})
        with self.assertRaises(IGProtocolError):
            self.client.wait_for_job(CONTEXT, "job", timeout=1)

    def test_artifact_projection_and_content(self):
        for media in ("image", "video"):
            self.transport.request.return_value = artifact(media=media)
            result = self.client.get_artifact(CONTEXT, "artifact")
            self.assertEqual((result.media_type, result.job_id, result.execution_id), (media, "job", "execution"))
            self.assertNotIn("private", json.dumps(asdict(result)))
            self.assertNotIn("url", result.preview)
        self.transport.request.return_value = HTTPResponse(200, {"Content-Type": "video/mp4"}, b"media")
        self.assertEqual(self.client.get_artifact_content(CONTEXT, "artifact"), b"media")

    def test_preview_preserves_processing_vs_bytes(self):
        self.transport.request.return_value = response({"status": "processing"}, 202, {"Retry-After": "3"})
        result = self.client.get_artifact_preview(CONTEXT, "artifact")
        self.assertEqual(result.status, 202)
        self.assertEqual(json.loads(result.body)["status"], "processing")
        self.transport.request.return_value = HTTPResponse(200, {"Content-Type": "image/webp"}, b"webp")
        self.assertEqual(self.client.get_artifact_preview(CONTEXT, "artifact").body, b"webp")

    def test_media_neutral_orchestration_multiple_artifacts(self):
        self.transport.request.side_effect = [submitted("video"), response({"job_id": "job", "status": "completed"}),
            response([{"execution_id": "execution", "status": "completed", "attempt": 1, "metadata": {"artifact_id": "a"}},
                      {"execution_id": "execution-2", "status": "completed", "attempt": 2, "metadata": {"artifact_id": "b"}}]),
            artifact("a", "video"), artifact("b", "image")]
        result = self.client.generate_and_wait(CONTEXT, correlation_id="scene", request=VIDEO, timeout=2)
        self.assertEqual([item.artifact_id for item in result.artifacts], ["a", "b"])
        self.assertEqual(result.submission.correlation_id, "scene")

    def test_convenience_surfaces_terminal_failure_without_new_submission(self):
        for code in ("VIDEO_PENDING_TIMEOUT", "VIDEO_SUBMISSION_UNCERTAIN", "VIDEO_CHECKPOINT_INVALID"):
            self.transport.reset_mock()
            self.transport.request.side_effect = [submitted("video"), response({"job_id": "job", "status": "failed", "error_code": code})]
            with self.assertRaises(IGGenerationError) as caught:
                self.client.generate_and_wait(CONTEXT, correlation_id="scene", request=VIDEO, timeout=2)
            self.assertEqual(caught.exception.code, code)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(self.transport.request.call_count, 2)

    def test_editor_launch_parses_session_and_uses_narrow_scope(self):
        self.transport.request.return_value = response({"session_id": "session", "correlation_id": "scene",
            "launch_url": "https://ig.example.test/embed/generation/session#launch=opaque", "launch_expires_at": "later", "expires_at": "later"}, 201)
        session = self.client.create_editor_session(CONTEXT, EditorLaunchRequest("scene", "https://sandcastle.test", IMAGE))
        self.assertEqual(session.correlation_id, "scene")
        token = self.transport.request.call_args.kwargs["headers"]["Authorization"].split()[1]
        self.assertEqual(jwt.decode(token, KEY.public_key(), algorithms=["RS256"], audience="ig-api")["scope"], "editor:launch")
        self.assertNotIn(token, repr(session))
        self.assertNotIn("opaque", repr(session))

    def test_independent_import_has_no_app_or_database_dependency(self):
        import subprocess
        run = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import ig_client; assert not any(k == 'app' or k.startswith(('sqlalchemy', 'redis', 'rq', 'vercel')) for k in sys.modules)", str(CLIENT_ROOT)],
                             capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_requests_transport_disables_redirects_cookies_and_environment_auth(self):
        session = Mock()
        remote = Mock(status_code=200, headers={})
        remote.iter_content.return_value = [b"data"]
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=False)
        session.request.return_value = remote
        with patch("requests.Session") as factory:
            factory.return_value.__enter__.return_value = session
            result = RequestsTransport().request("GET", "https://ig.test/v2/artifacts/a/content", headers={}, body=None, timeout=1)
        self.assertFalse(session.trust_env)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertEqual(result.body, b"data")

    def test_requests_transport_limits_bytes(self):
        session = Mock()
        remote = Mock(status_code=200, headers={})
        remote.iter_content.return_value = [b"too large"]
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=False)
        session.request.return_value = remote
        with patch("requests.Session") as factory:
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(IGProtocolError):
                RequestsTransport(2).request("GET", "https://ig.test", headers={}, body=None, timeout=1)


# Integration adapter translates only HTTP plumbing. Real server auth, schemas,
# persistence, idempotency and response serialization remain active.
from tests import test_service_access as access
from app.auth.dependencies import get_current_user
from app.db.models import UserIdentity
from app.main import app
from app.ops.service_credentials import register_credential


class TestServerTransport:
    def __init__(self, server):
        self.server = server

    def request(self, method, url, *, headers, body, timeout):
        result = self.server.request(method, url, headers=headers, content=body, follow_redirects=False)
        return HTTPResponse(result.status_code, dict(result.headers), result.content)


class ClientServerTests(access.contracts.ImageExecutionTestCase):
    def setUp(self):
        super().setUp()
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        self.storage = access.storage_tests.install_storage(self)
        self.enterContext(patch("app.images.tasks.artifact_store", self.storage))
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="identity-one", user_id="user-one", issuer=CONTEXT.actor_issuer, subject=CONTEXT.actor_subject))
            register_credential(db, key_id="sandcastle-key-1", service_id="sandcastle", public_key=KEY.public_key().public_bytes(
                access.serialization.Encoding.PEM, access.serialization.PublicFormat.SubjectPublicKeyInfo),
                scopes=list(contract.SCOPES), tenant_ids=["tenant-one"], actor_issuers=[CONTEXT.actor_issuer])
            db.commit()
        self.helper = IGClient("https://testserver", ServiceCredentials("sandcastle", "sandcastle-key-1", lambda: KEY),
                               transport=TestServerTransport(self.client), retry_interval=0.001)

    def test_discovery_parses_actual_image_video_contract(self):
        capabilities = self.helper.discover_capabilities(CONTEXT)
        media = {item["id"]: item for item in capabilities["media_types"]}
        self.assertIn("image", media)
        self.assertIn("video", media)
        model = media["image"]["providers"][0]["models"][0]
        self.assertIn("operations", model["technical"])
        self.assertIn("reference_inputs", model["technical"])

    def test_image_idempotency_conflict_and_completed_result(self):
        first = self.helper.submit_generation(CONTEXT, correlation_id="scene-1", request=IMAGE)
        again = self.helper.submit_generation(CONTEXT, correlation_id="scene-1", request=IMAGE)
        self.assertEqual(first.job_id, again.job_id)
        with self.assertRaises(IGHTTPError) as caught:
            self.helper.submit_generation(CONTEXT, correlation_id="scene-1", request={**IMAGE, "intent": {"description": "changed"}})
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        access.execute_job(first.job_id)
        result = self.helper.generate_and_wait(CONTEXT, correlation_id="scene-1", request=IMAGE, timeout=3)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].media_type, "image")
        self.assertEqual(self.helper.get_execution(CONTEXT, result.executions[0].execution_id), result.executions[0])
        self.assertTrue(self.helper.get_artifact_content(CONTEXT, result.artifacts[0].artifact_id))
        self.provider.generate.assert_called_once()

    def test_actual_authorization_and_not_found_errors(self):
        for context, code in ((DelegatedContext(CONTEXT.actor_issuer, "unknown", "tenant-one"), "ACTOR_NOT_AUTHORIZED"),
                              (DelegatedContext(CONTEXT.actor_issuer, CONTEXT.actor_subject, "tenant-two"), "TENANT_NOT_AUTHORIZED")):
            with self.assertRaises(IGHTTPError) as caught:
                self.helper.discover_capabilities(context)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(IGHTTPError) as caught:
            self.helper.get_job(CONTEXT, "missing")
        self.assertEqual(caught.exception.status, 404)

    def test_real_server_rejects_expired_helper_token(self):
        with patch("ig_client.auth.time.time", return_value=time.time() - 600):
            expired = create_service_token(service_id="sandcastle", key_id="sandcastle-key-1", private_key=KEY,
                context=CONTEXT, scopes=["media:discover"])
        credentials = Mock()
        credentials.token.return_value = expired
        helper = IGClient("https://testserver", credentials, transport=TestServerTransport(self.client))
        with self.assertRaises(IGHTTPError) as caught:
            helper.discover_capabilities(CONTEXT)
        self.assertEqual((caught.exception.status, caught.exception.code), (401, "INVALID_SERVICE_CREDENTIAL"))

    def test_real_server_insufficient_route_scope_normalized(self):
        credentials = Mock()
        credentials.token.return_value = self.helper.credentials.token(CONTEXT, ["jobs:read"])
        helper = IGClient("https://testserver", credentials, transport=TestServerTransport(self.client))
        with self.assertRaises(IGHTTPError) as caught:
            helper.discover_capabilities(CONTEXT)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "INSUFFICIENT_SCOPE"))

    def test_response_loss_recovers_same_persisted_job_for_both_queue_contracts(self):
        from app.jobs.vercel_queue import VercelJobQueue
        from app.jobs.rq_queue import RQJobQueue
        for adapter in (RQJobQueue, VercelJobQueue):
            with self.subTest(adapter=adapter.__name__):
                queue = Mock(spec=adapter)
                delegate = TestServerTransport(self.client)
                seen = []
                def lost_response(*args, **kwargs):
                    result = delegate.request(*args, **kwargs)
                    seen.append(json.loads(result.body)["job_id"])
                    if len(seen) == 1:
                        raise IGTransportError("response lost after commit")
                    return result
                transport = Mock()
                transport.request.side_effect = lost_response
                helper = IGClient("https://testserver", self.helper.credentials, transport=transport, retry_interval=0.001)
                with patch("app.media.submissions.get_job_queue", return_value=queue):
                    result = helper.submit_generation(CONTEXT, correlation_id=adapter.__name__, request=IMAGE)
                self.assertEqual(seen, [result.job_id, result.job_id])
                queue.enqueue.assert_called_once_with(result.job_id)
                access.execute_job(result.job_id)
                completed = self.helper.generate_and_wait(CONTEXT, correlation_id=adapter.__name__, request=IMAGE, timeout=3)
                self.assertEqual(completed.job.status, "completed")
                self.assertEqual(completed.artifacts[0].media_type, "image")
                self.assertEqual(completed.artifacts[0].job_id, result.job_id)


class ClientVideoServerTests(access.video_tests.VideoTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("app.artifacts.service.SessionLocal", self.sessions))
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="identity-one", user_id="user-one", issuer=CONTEXT.actor_issuer, subject=CONTEXT.actor_subject))
            register_credential(db, key_id="sandcastle-key-1", service_id="sandcastle", public_key=KEY.public_key().public_bytes(
                access.serialization.Encoding.PEM, access.serialization.PublicFormat.SubjectPublicKeyInfo),
                scopes=list(contract.SCOPES), tenant_ids=["tenant-one"], actor_issuers=[CONTEXT.actor_issuer])
            db.commit()
        self.helper = IGClient("https://testserver", ServiceCredentials("sandcastle", "sandcastle-key-1", lambda: KEY),
                               transport=TestServerTransport(self.client), retry_interval=0.001)

    def test_video_first_frame_retry_poll_and_media_neutral_result(self):
        from app.jobs.continuation import continue_job
        from tests.video_support import HTTPResponse as ProviderResponse, MP4, TEMP_VIDEO_URL, operation_response
        request = {**VIDEO, "references": [{"artifact_id": self.image_reference(), "role": "first_frame"}]}
        submission = self.helper.submit_generation(CONTEXT, correlation_id="video-scene", request=request)
        self.http.side_effect = None
        self.http.return_value = operation_response()
        access.execute_job(submission.job_id)
        retry = self.helper.submit_generation(CONTEXT, correlation_id="video-scene", request=request)
        self.assertEqual((retry.job_id, retry.status), (submission.job_id, "waiting"))
        executions = self.helper.get_job_executions(CONTEXT, submission.job_id)
        execution_id, metadata = self.current_execution()
        self.assertEqual(executions[0].execution_id, execution_id)
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = ProviderResponse({}, chunks=[MP4], headers={"Content-Type": "video/mp4"})
        continue_job(submission.job_id, metadata["continuation"]["id"])
        result = self.helper.generate_and_wait(CONTEXT, correlation_id="video-scene", request=request, timeout=3)
        self.assertEqual(result.job.status, "completed")
        self.assertEqual(result.artifacts[0].media_type, "video")
        self.assertEqual(result.artifacts[0].execution_id, execution_id)
        self.assertEqual(sum(call.args[0] == "POST" for call in self.http.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
