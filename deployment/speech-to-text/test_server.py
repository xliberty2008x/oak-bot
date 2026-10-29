import datetime
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import wave

import server


def wav(seconds=1):
    data = io.BytesIO()
    with wave.open(data, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0\0' * int(seconds * 16000))
    return data.getvalue()


class AudioTests(unittest.TestCase):
    def test_documented_primary_multipart_fields(self):
        body, _ = server.multipart(wav(), True)
        self.assertIn(b'name="languages[]"\r\n\r\nuk', body)
        self.assertIn(b'name="model"\r\n\r\ngpt-transcribe', body)
        self.assertIn(b'name="stream"\r\n\r\nfalse', body)
        self.assertIn(b'filename="voice.wav"', body)
        self.assertNotIn(b'name="language"', body)
        self.assertNotIn(b'name="keywords', body)

    def test_native_language_and_normalized_wav_boundary(self):
        for seconds in (1, 60):
            audio = wav(seconds)
            body, content_type = server.multipart(audio, False)
            self.assertEqual(server.parse_audio(content_type, body), (audio, seconds))
        body, content_type = server.multipart(wav(60.01), False)
        with self.assertRaises(ValueError):
            server.parse_audio(content_type, body)

    def test_invalid_language_truncated_file_and_duplicate_field(self):
        body, content_type = server.multipart(wav(), False)
        for invalid in (body.replace(b'uk-UA', b'en-US'),
                        body.replace(b'name="response_format"', b'name="language"')):
            with self.assertRaises((ValueError, wave.Error, EOFError)):
                server.parse_audio(content_type, invalid)
        truncated, truncated_type = server.multipart(wav()[:-2], False)
        with self.assertRaises((ValueError, wave.Error, EOFError)):
            server.parse_audio(truncated_type, truncated)
        with self.assertRaises(ValueError):
            server.parse_audio(content_type, b'x' * (server.MAX_BODY + 1))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / 'private'
        self.now = datetime.datetime(2026, 10, 29, tzinfo=server.ZoneInfo('Europe/Kyiv'))
        self.budget = server.Budget(self.directory, minutes=1, now=lambda: self.now)
    def tearDown(self):
        self.temp.cleanup()

    def test_persistent_limit_and_private_aggregate_only(self):
        self.assertTrue(self.budget.reserve(30))
        self.assertTrue(self.budget.reserve(30))
        self.assertFalse(self.budget.reserve(0.001))
        self.assertFalse(server.Budget(self.directory, 1, now=lambda: self.now).available())
        path = self.directory / 'budget.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()),
                         {'month': '2026-10', 'reserved_audio_ms': 60000, 'requests': 2})

    def test_kyiv_month_rollover_and_clock_rollback(self):
        self.assertTrue(self.budget.reserve(60))
        self.now = datetime.datetime(2026, 11, 1, tzinfo=server.ZoneInfo('Europe/Kyiv'))
        self.assertTrue(self.budget.reserve(30))
        self.now = datetime.datetime(2026, 10, 31, tzinfo=server.ZoneInfo('Europe/Kyiv'))
        self.assertFalse(self.budget.reserve(1))

    def test_corrupt_or_symlink_ledger_fails_closed(self):
        self.directory.mkdir(mode=0o700)
        path = self.directory / 'budget.json'
        path.write_text('corrupt')
        path.chmod(0o600)
        self.assertFalse(self.budget.reserve(1))
        path.unlink()
        target = Path(self.temp.name) / 'target'
        target.write_text('{}')
        path.symlink_to(target)
        self.assertFalse(self.budget.reserve(1))
        self.assertEqual(target.read_text(), '{}')

    def test_parallel_reservations_never_overspend(self):
        results = []
        workers = [threading.Thread(target=lambda: results.append(self.budget.reserve(30))) for _ in range(8)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertEqual(results.count(True), 2)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.budget = Mock()
        self.budget.reserve.return_value = True
        self.budget.available.return_value = True
        self.broker = server.Broker(self.budget)

    def test_primary_success_and_empty_success_never_fall_back(self):
        for text in ('recognized', ''):
            with patch.object(self.broker, 'primary_configured', return_value=True), \
                    patch.object(self.broker, 'paid', return_value=text) as paid, \
                    patch.object(self.broker, 'local', side_effect=AssertionError('no fallback')):
                result = self.broker.transcribe(wav(), 1)
            self.assertEqual(result['provider'], 'openai')
            self.assertEqual(result['text'], text)
            self.assertIsNone(result['fallback_reason'])
            paid.assert_called_once()

    def test_reservation_precedes_paid_call_and_unknown_outcome_counts(self):
        sequence = []
        self.budget.reserve.side_effect = lambda seconds: sequence.append(('reserved', seconds)) or True
        def fail(_audio):
            sequence.append(('paid', None))
            raise server.Unavailable('primary_timeout')
        with patch.object(self.broker, 'primary_configured', return_value=True), \
                patch.object(self.broker, 'paid', side_effect=fail), \
                patch.object(self.broker, 'local', return_value='local'):
            result = self.broker.transcribe(wav(), 1)
        self.assertEqual(sequence, [('reserved', 1), ('paid', None)])
        self.assertEqual(result['fallback_reason'], 'primary_timeout')
        self.budget.reserve.assert_called_once()

    def test_all_primary_failure_classes_use_local(self):
        for reason in ('network_error', 'authentication_or_quota', 'rate_or_quota_limit',
                       'provider_unavailable', 'malformed_response', 'primary_timeout'):
            with patch.object(self.broker, 'primary_configured', return_value=True), \
                    patch.object(self.broker, 'paid', side_effect=server.Unavailable(reason)), \
                    patch.object(self.broker, 'local', return_value='local'):
                result = self.broker.transcribe(wav(), 1)
            self.assertEqual(result['provider'], 'nemotron')
            self.assertEqual(result['fallback_reason'], reason)

    def test_missing_key_or_budget_never_calls_paid(self):
        for configured, budget in ((False, True), (True, False)):
            self.budget.reserve.return_value = budget
            with patch.object(self.broker, 'primary_configured', return_value=configured), \
                    patch.object(self.broker, 'paid', side_effect=AssertionError('no paid call')), \
                    patch.object(self.broker, 'local', return_value='local'):
                result = self.broker.transcribe(wav(), 1)
            self.assertEqual(result['fallback_reason'], 'local_budget_exhausted' if configured else 'not_configured')

    def test_ready_uses_local_health_when_primary_not_configured(self):
        with patch.object(self.broker, 'primary_configured', return_value=False), \
                patch.object(self.broker, 'local', return_value=True):
            result = self.broker.ready()
        self.assertTrue(result['ready'])
        self.assertFalse(result['primary_configured'])
        self.assertEqual(result['counts'], {'openai': 0, 'nemotron': 0, 'unavailable': 0})

    def test_primary_readiness_never_waits_for_fallback(self):
        with patch.object(self.broker, 'primary_configured', return_value=True), \
                patch.object(self.broker, 'local', side_effect=AssertionError('must not probe fallback')):
            result = self.broker.ready()
        self.assertTrue(result['ready'])
        self.assertIsNone(result['fallback_ready'])

    def test_exhausted_budget_readiness_probes_fallback(self):
        self.budget.available.return_value = False
        with patch.object(self.broker, 'primary_configured', return_value=True), \
                patch.object(self.broker, 'local', return_value=True) as fallback:
            result = self.broker.ready()
        self.assertTrue(result['ready'])
        self.assertFalse(result['primary_budget_available'])
        self.assertTrue(result['fallback_ready'])
        fallback.assert_called_once_with()

    def test_paid_timeout_actually_kills_and_reaps_worker(self):
        process = Mock(returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', 20), (b'', None)]
        with patch('server.subprocess.Popen', return_value=process) as spawn:
            with self.assertRaisesRegex(server.Unavailable, '^primary_timeout$'):
                self.broker.paid(wav())
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertEqual(spawn.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_paid_worker_rejects_invalid_result_without_exposing_body(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (b'PRIVATE provider error body', None)
        with patch('server.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(server.Unavailable, '^malformed_response$'):
                self.broker.paid(wav())

    def test_fallback_absolute_deadline_closes_connection(self):
        stopped = threading.Event()
        connection = Mock()
        connection.active_socket = connection.sock
        connection.active_socket.shutdown.side_effect = lambda *_args: stopped.set()
        response = Mock(status=200)
        def read(_limit):
            stopped.wait(2)
            return b'{"text":""}'
        response.read.side_effect = read
        connection.getresponse.return_value = response
        with patch('server.UnixConnection', return_value=connection), patch('server.FALLBACK_DEADLINE', 0.02):
            with self.assertRaisesRegex(server.Unavailable, '^fallback_unavailable$'):
                self.broker.local(wav())
        connection.sock.shutdown.assert_called_once()
        connection.close.assert_called_once()

    def test_fallback_deadline_interrupts_connection_close_response_body(self):
        # A real HTTPConnection sets .sock=None after Connection: close headers.
        # Stream bytes faster than its per-read timeout so only the absolute
        # watchdog, using the retained active socket, can interrupt the body.
        import http.server
        import time
        body_started = threading.Event()
        stop_sender = threading.Event()
        observations = []
        class DelayedBody(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', '1000')
                self.send_header('Connection', 'close')
                self.end_headers()
                body_started.set()
                try:
                    while not stop_sender.wait(0.01):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                except OSError:
                    pass
        original = server.UnixConnection
        class ObservedConnection(original):
            def getresponse(self):
                response = super().getresponse()
                observations.append((self.sock is None, self.active_socket is not None))
                return response
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'delayed.sock')
            listener = server.UnixServer(path, DelayedBody)
            thread = threading.Thread(target=listener.serve_forever, daemon=True)
            thread.start()
            started = time.monotonic()
            try:
                with patch('server.FALLBACK_PATH', path), patch('server.FALLBACK_DEADLINE', 0.15), \
                        patch('server.UnixConnection', ObservedConnection):
                    with self.assertRaisesRegex(server.Unavailable, '^fallback_unavailable$'):
                        self.broker.local(wav())
                elapsed = time.monotonic() - started
            finally:
                stop_sender.set()
                listener.shutdown()
                listener.server_close()
            self.assertTrue(body_started.is_set())
            self.assertEqual(observations, [(True, True)])
            self.assertLess(elapsed, 1)


class WorkerProtocolTests(unittest.TestCase):
    def test_error_statuses_are_safe_codes_and_never_read_error_body(self):
        for status, reason in ((401, 'authentication_or_quota'), (403, 'authentication_or_quota'),
                               (429, 'rate_or_quota_limit'), (503, 'provider_unavailable'),
                               (302, 'provider_error')):
            error = urllib.error.HTTPError('https://api.openai.com/v1/audio/transcriptions', status,
                                            'PRIVATE', {}, io.BytesIO(b'PRIVATE body'))
            opener = Mock()
            opener.open.side_effect = error
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-value'}), \
                    patch('server.sys.stdin', Mock(buffer=io.BytesIO(wav()))), \
                    patch('server.urllib.request.build_opener', return_value=opener):
                result = server.openai_worker()
            self.assertEqual(result, {'ok': False, 'reason': reason})
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_empty_json_transcript_is_valid(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"text":"","usage":{"seconds":1}}'
        opener = Mock()
        opener.open.return_value = response
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-value'}), \
                patch('server.sys.stdin', Mock(buffer=io.BytesIO(wav()))), \
                patch('server.urllib.request.build_opener', return_value=opener):
            self.assertEqual(server.openai_worker(), {'ok': True, 'text': ''})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/audio/transcriptions')
        self.assertEqual(request.method, 'POST')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'broker.sock')
        self.http = server.UnixServer(self.path, server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.broker = Mock()
        self.broker.transcribe.return_value = {'text': 'recognized', 'provider': 'openai'}
        self.original = server.BROKER
        server.BROKER = self.broker
    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        server.BROKER = self.original
        self.temp.cleanup()
    def request(self, body=None, headers=None):
        connection = http.client.HTTPConnection('localhost', timeout=3)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(3)
        connection.sock.connect(self.path)
        try:
            # A declared oversize upload must be rejected without sending its body.
            payload = b'' if headers and 'Content-Length' in headers else body or b'x'
            connection.request('POST', '/v1/audio/transcriptions', body=payload, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def test_inference_busy_is_rejected_without_provider_call(self):
        server.INFERENCE.acquire()
        try:
            self.assertEqual(self.request()[0], 503)
        finally:
            server.INFERENCE.release()
        self.broker.transcribe.assert_not_called()

    def test_handler_count_bounded_before_thread_creation(self):
        for _ in range(4): server.HANDLERS.acquire()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(self.path)
                self.assertTrue(connection.recv(1024).startswith(b'HTTP/1.1 503'))
        finally:
            for _ in range(4): server.HANDLERS.release()
        self.broker.transcribe.assert_not_called()

    def test_concurrent_second_upload_is_rejected_while_first_runs(self):
        started, released = threading.Event(), threading.Event()
        results = []
        def slow_provider(*_args):
            started.set()
            released.wait(3)
            return {'text': 'recognized', 'provider': 'openai'}
        self.broker.transcribe.side_effect = slow_provider
        body, content_type = server.multipart(wav(), False)
        worker = threading.Thread(target=lambda: results.append(self.request(body, {'Content-Type': content_type})))
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            self.assertEqual(self.request(body, {'Content-Type': content_type})[0], 503)
        finally:
            released.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0][0], 200)
        self.broker.transcribe.assert_called_once()

    def test_invalid_file_then_success_recovers_and_upload_limit_is_early(self):
        self.assertEqual(self.request(headers={'Content-Length': str(server.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request()[0], 400)
        body, content_type = server.multipart(wav(), False)
        status, result = self.request(body, {'Content-Type': content_type})
        self.assertEqual(status, 200)
        self.assertEqual(result['provider'], 'openai')
        self.broker.transcribe.assert_called_once()

    def test_provider_exception_is_generic_in_http_response(self):
        self.broker.transcribe.side_effect = server.Unavailable('PRIVATE provider error')
        body, content_type = server.multipart(wav(), False)
        status, result = self.request(body, {'Content-Type': content_type})
        self.assertEqual((status, result), (503, {'error': {'code': 'asr_unavailable'}}))


if __name__ == '__main__':
    unittest.main()
