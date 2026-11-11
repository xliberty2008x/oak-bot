import json
import unittest
from unittest.mock import Mock, patch

from oak.voice_realtime import RealtimeControlError, RealtimeSipClient, validate_call_id


class RealtimeSipTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch("oak.voice_realtime.http.client.HTTPSConnection")
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.conn = self.factory.return_value
        self.conn.getresponse.return_value.status = 200
        self.authorize = Mock(return_value=True)
        self.client = RealtimeSipClient(api_key="test-credential", live_enabled=True,
                                        authorize=self.authorize, timeout=4)

    def test_disabled_without_network_or_authorization(self):
        client = RealtimeSipClient(api_key="test", authorize=self.authorize)
        with self.assertRaisesRegex(RealtimeControlError, "disabled"):
            client.hangup("rtc_123")
        self.authorize.assert_not_called()
        self.factory.assert_not_called()

    def test_credential_and_authorization_required(self):
        for kwargs in ({}, {"api_key": "test"},
                       {"api_key": "test", "authorize": lambda *args: "yes"}):
            with self.assertRaises(RealtimeControlError):
                RealtimeSipClient(live_enabled=True, **kwargs).hangup("rtc_123")
        self.factory.assert_not_called()

    def test_accept_exact_payload_is_authorized_and_sent(self):
        self.client.accept("rtc_123", model="gpt-realtime-2.1", instructions="Говори українською.")
        args, kwargs = self.conn.request.call_args
        self.assertEqual(args, ("POST", "/v1/realtime/calls/rtc_123/accept"))
        body = kwargs["body"]
        self.assertEqual(json.loads(body), {"type": "realtime", "model": "gpt-realtime-2.1",
                         "instructions": "Говори українською.", "audio": {"output": {"voice": "marin"}}})
        self.authorize.assert_called_once_with("rtc_123", "accept", body)
        self.factory.assert_called_once_with("api.openai.com", timeout=4)
        self.conn.getresponse.return_value.read.assert_not_called()
        self.conn.close.assert_called_once()

    def test_reject_and_hangup(self):
        self.client.reject("rtc_123")
        self.assertEqual(json.loads(self.conn.request.call_args.kwargs["body"]), {"status_code": 603})
        self.client.hangup("rtc_123")
        self.assertIsNone(self.conn.request.call_args.kwargs["body"])
        self.assertEqual(self.conn.request.call_args.args[1], "/v1/realtime/calls/rtc_123/hangup")
        self.authorize.assert_called_with("rtc_123", "hangup", None)

    def test_call_id_injection_rejected(self):
        for value in ("", "../accept", "x?key=y", "x/y", "x\n", "☃", "x" * 257, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.hangup(value)
        self.factory.assert_not_called()
        self.assertEqual(validate_call_id("rtc_u1-123"), "rtc_u1-123")

    def test_invalid_configuration(self):
        for timeout in (0, -1, 31, float("nan"), float("inf"), True, "10"):
            with self.assertRaises(ValueError):
                RealtimeSipClient(timeout=timeout)
        for key in ("test\r\nX: secret", "a b", "☃", None):
            with self.assertRaises(ValueError):
                RealtimeSipClient(api_key=key)
        with self.assertRaises(ValueError):
            RealtimeSipClient(live_enabled="true")
        for status in (True, 299, 700, "486"):
            with self.assertRaises(ValueError):
                self.client.reject("rtc_123", status_code=status)
        self.factory.assert_not_called()

    def test_redirects_and_errors_are_not_retried_or_exposed(self):
        for status in (302, 401, 429, 500):
            self.conn.reset_mock()
            self.conn.getresponse.return_value.status = status
            with self.assertRaises(RealtimeControlError) as error:
                self.client.hangup("rtc_123")
            self.assertTrue(error.exception.outcome_uncertain)
            self.assertEqual(error.exception.status_code, status)
            self.conn.request.assert_called_once()
            self.conn.getresponse.return_value.read.assert_not_called()

    def test_timeout_is_redacted_and_uncertain(self):
        self.conn.getresponse.side_effect = TimeoutError("test-credential private prompt")
        with self.assertRaises(RealtimeControlError) as error:
            self.client.hangup("rtc_123")
        self.assertNotIn("test-credential", str(error.exception))
        self.assertTrue(error.exception.outcome_uncertain)
        self.conn.request.assert_called_once()
        self.conn.close.assert_called_once()

    def test_authorizer_error_is_redacted_and_no_network(self):
        self.authorize.side_effect = RuntimeError("private policy details")
        with self.assertRaises(RealtimeControlError) as error:
            self.client.hangup("rtc_123")
        self.assertNotIn("private", str(error.exception))
        self.assertFalse(error.exception.outcome_uncertain)
        self.factory.assert_not_called()


class WebhookVerificationTests(unittest.TestCase):
    def setUp(self):
        from oak.voice_realtime import verify_incoming_webhook
        self.verify = verify_incoming_webhook
        self.sdk = Mock()
        self.sdk.webhooks.unwrap.return_value.type = "realtime.call.incoming"
        self.sdk.webhooks.unwrap.return_value.id = "evt_123"
        self.sdk.webhooks.unwrap.return_value.data.call_id = "rtc_123"
        self.headers = {"Webhook-Id": "wh_123", "Webhook-Timestamp": "1234",
                        "Webhook-Signature": "v1,test"}

    def run_verify(self, body=b'{ "raw": true }', headers=None):
        return self.verify(body, self.headers if headers is None else headers,
                           sdk_client=self.sdk, webhook_secret="test-secret")

    def test_raw_bytes_and_secret_pass_to_sdk_without_parsing(self):
        result = self.run_verify()
        self.assertEqual(result, {"call_id": "rtc_123", "event_id": "evt_123", "webhook_id": "wh_123"})
        self.sdk.webhooks.unwrap.assert_called_once_with(b'{ "raw": true }',
            {key.lower(): value for key, value in self.headers.items()}, secret="test-secret")

    def test_sdk_signature_failure_has_no_fallback(self):
        self.sdk.webhooks.unwrap.side_effect = ValueError("secret private payload")
        with self.assertRaises(RealtimeControlError) as error:
            self.run_verify()
        self.assertNotIn("private", str(error.exception))

    def test_malformed_body_or_headers_fail_before_sdk(self):
        for body in ("{}", {}, b"", b"x" * 262145):
            with self.assertRaises(RealtimeControlError):
                self.run_verify(body=body)
        for headers in ({}, {**self.headers, "webhook-id": "duplicate"},
                        {**self.headers, "Webhook-Id": "x\n"}):
            with self.assertRaises(RealtimeControlError):
                self.run_verify(headers=headers)
        self.sdk.webhooks.unwrap.assert_not_called()

    def test_wrong_event_and_bad_call_id_fail_closed(self):
        self.sdk.webhooks.unwrap.return_value.type = "live.transport.incoming"
        with self.assertRaises(RealtimeControlError):
            self.run_verify()
        self.sdk.webhooks.unwrap.return_value.type = "realtime.call.incoming"
        self.sdk.webhooks.unwrap.return_value.data.call_id = "../bad"
        with self.assertRaises(RealtimeControlError):
            self.run_verify()


if __name__ == "__main__":
    unittest.main()
