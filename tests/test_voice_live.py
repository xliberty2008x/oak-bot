import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from oak.voice_live import LiveSipClient, validate_provider_url
from oak.voice_realtime import RealtimeControlError


class LiveSipTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("oak.voice_live.http.client.HTTPSConnection")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = self.factory.return_value
        self.response = self.conn.getresponse.return_value
        self.response.status = 201
        self.response.read.return_value = b'{"session":{"id":"live_123"},"transport":{"type":"sip"}}'
        self.authorize = Mock(return_value=True)
        self.client = LiveSipClient(api_key="dummy-key", live_enabled=True, authorize=self.authorize)
        self.args = dict(intent_id="intent_123", destination="+12025550123", caller_number="+12025550124",
                         provider_url="sips:sip.example.com:5061", username="dummy-user", password="dummy-password",
                         model="gpt-live-1", instructions="Говори українською.")

    def test_start_exact_payload_and_redacted_authorization(self):
        self.assertEqual(self.client.start(**self.args), "live_123")
        args, kwargs = self.conn.request.call_args
        self.assertEqual(args, ("POST", "/v1/live/sessions"))
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload, {"session": {"model":"gpt-live-1", "instructions":self.args["instructions"],
            "audio":{"output":{"voice":"marin"}}, "delegation":{"type":"client"}, "store":False},
            "transport":{"type":"sip", "destination":self.args["destination"], "trunk":{
                "provider_url":self.args["provider_url"], "auth":{"type":"digest","username":"dummy-user",
                "password":"dummy-password"}, "caller_number":self.args["caller_number"]}}})
        approved = self.authorize.call_args.args[0]
        self.assertEqual(approved.payload_sha256, hashlib.sha256(kwargs["body"]).hexdigest())
        for key in ("destination", "caller_number", "model", "provider_url", "intent_id"):
            self.assertEqual(getattr(approved, key), self.args[key])
        self.assertNotIn("dummy-password", repr(approved))
        self.assertFalse(hasattr(approved, "password"))
        self.assertFalse(hasattr(approved, "instructions"))
        self.factory.assert_called_once_with("api.openai.com", timeout=10.0)
        self.response.read.assert_called_once_with(16385)
        self.conn.close.assert_called_once()

    def test_guide_200_and_reference_201_supported(self):
        self.response.status = 200
        self.assertEqual(self.client.start(**self.args), "live_123")

    def test_hangup_has_no_body_and_exact_authorization(self):
        self.response.status = 200
        self.client.hangup("live_123", intent_id="end_123")
        self.assertEqual(self.conn.request.call_args.args, ("POST", "/v1/live/sessions/live_123/hangup"))
        self.assertIsNone(self.conn.request.call_args.kwargs["body"])
        action = self.authorize.call_args.args[0]
        self.assertEqual((action.action, action.session_id, action.intent_id), ("hangup", "live_123", "end_123"))
        self.response.read.assert_not_called()

    def test_opaque_session_ids_roundtrip_and_encode_once(self):
        for value, encoded in (("live/☃?x#y%20", "live%2F%E2%98%83%3Fx%23y%2520"),
                               (".", "%2E"), ("..", "%2E%2E"), ("...", "%2E%2E%2E"),
                               (" x\n", "%20x%0A"), ("x" * 4096, "x" * 4096)):
            with self.subTest(value=value):
                self.response.status = 201
                self.response.read.return_value = json.dumps({"session":{"id":value},
                                                             "transport":{"type":"sip"}}).encode()
                self.assertEqual(self.client.start(**self.args), value)
                self.response.status = 200
                self.client.hangup(value, intent_id="end")
                self.assertEqual(self.conn.request.call_args.args[1],
                                 "/v1/live/sessions/" + encoded + "/hangup")
                self.assertEqual(self.authorize.call_args.args[0].session_id, value)

    def test_invalid_session_ids_in_creation_response_are_uncertain(self):
        for value in ("\ud800", "x" * 4097, "☃" * 1366):
            self.response.read.return_value = json.dumps({"session":{"id":value},
                                                         "transport":{"type":"sip"}}).encode()
            with self.assertRaises(RealtimeControlError) as raised:
                self.client.start(**self.args)
            self.assertTrue(raised.exception.outcome_uncertain)

    def test_default_and_denial_make_no_network_request(self):
        for client in (LiveSipClient(), LiveSipClient(live_enabled=True),
                       LiveSipClient(live_enabled=True, api_key="test")):
            with self.assertRaises(RealtimeControlError):
                client.start(**self.args)
        for approval in (False, "yes", 1, None):
            self.authorize.return_value = approval
            with self.assertRaises(RealtimeControlError):
                self.client.start(**self.args)
        self.factory.assert_not_called()

    def test_invalid_call_inputs(self):
        for key, value in (("destination","sip:x@example.com"), ("caller_number","0501234567"),
                           ("destination","+012"), ("intent_id","../x"), ("username","x\r"),
                           ("password","x\0"), ("password","x" * 4097), ("model",""),
                           ("instructions", ""), ("voice", "a\n")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.client.start(**{**self.args, key:value})
        for session_id in ("", None, "\ud800", "x" * 4097):
            with self.assertRaises(ValueError):
                self.client.hangup(session_id, intent_id="end")
        self.authorize.assert_not_called()
        self.factory.assert_not_called()

    def test_endpoint_validation(self):
        for url in ("sip:sip.example.com", "sips:user@host.com", "sips:a.com/path", "sips:a.com?x=y",
                    "sips:a.com;transport=udp", "sips:127.0.0.1", "sips:[::1]", "sips:10.0.0.1",
                    "sips:localhost", "sips:foo.local", "sips:a.com:0", "sips:a.com:65536",
                    "sips:224.0.0.1", "sips:a..com", "sips:0x7f000001", "sips:127.1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_provider_url(url)
        for url in ("sips:sip.example.com", "sips:sip.example.com:5061;transport=tcp", "sips:8.8.8.8"):
            self.assertEqual(validate_provider_url(url), url)

    def test_error_and_redirect_are_never_followed_or_retried(self):
        for status in (302, 401, 429, 500):
            self.conn.reset_mock()
            self.response.status = status
            with self.assertRaises(RealtimeControlError) as raised:
                self.client.start(**self.args)
            self.assertEqual(raised.exception.status_code, status)
            self.assertTrue(raised.exception.outcome_uncertain)
            self.conn.request.assert_called_once()
            self.response.read.assert_not_called()

    def test_timeout_redaction_and_uncertainty(self):
        self.conn.getresponse.side_effect = TimeoutError("dummy-password")
        with self.assertRaises(RealtimeControlError) as raised:
            self.client.start(**self.args)
        self.assertNotIn("dummy-password", str(raised.exception))
        self.assertTrue(raised.exception.outcome_uncertain)
        self.conn.request.assert_called_once()
        self.conn.close.assert_called_once()

    def test_bad_response_is_uncertain(self):
        for response in (b"x" * 16385, b"not-json", b"[]", b"{}", b"\xff",
                         b'{"session":{"id":""},"transport":{"type":"sip"}}',
                         b'{"session":{"id":"live_123"},"transport":{"type":"webrtc"}}',
                         b'{"session":{"id":"live_123","id":"other"},"transport":{"type":"sip"}}'):
            self.response.read.return_value = response
            with self.subTest(response=response[:40]), self.assertRaises(RealtimeControlError) as raised:
                self.client.start(**self.args)
            self.assertTrue(raised.exception.outcome_uncertain)

    def test_nonfinite_response_is_uncertain(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            self.response.read.return_value = (
                '{"session":{"id":"live_123"},"transport":{"type":"sip"},"extra":'
                + constant + '}'
            ).encode("utf-8")
            with self.subTest(constant=constant), self.assertRaises(RealtimeControlError) as raised:
                self.client.start(**self.args)
            self.assertTrue(raised.exception.outcome_uncertain)

    def test_changed_secret_or_prompt_changes_fingerprint(self):
        fingerprints = []
        for extra in ({}, {"password":"changed-secret"}, {"instructions":"Changed prompt"}):
            self.client.start(**{**self.args, **extra})
            fingerprints.append(self.authorize.call_args.args[0].payload_sha256)
        self.assertEqual(len(set(fingerprints)), 3)

    def test_authorization_failure_redacted(self):
        self.authorize.side_effect = ValueError("private policy")
        with self.assertRaises(RealtimeControlError) as raised:
            self.client.start(**self.args)
        self.assertFalse(raised.exception.outcome_uncertain)
        self.assertNotIn("private", str(raised.exception))
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
