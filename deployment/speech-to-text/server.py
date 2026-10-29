#!/usr/bin/python3
"""Trusted Oak audio broker: OpenAI primary, private local NeMo fallback."""
import datetime
import email.parser
import email.policy
import fcntl
import http.client
import http.server
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from zoneinfo import ZoneInfo

BASE = Path('/home/ubuntu/oak-stt-openai')
SOCKET_PATH = '/run/oak-stt/asr.sock'
FALLBACK_PATH = '/run/kira-asr/asr.sock'
MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 65536
MAX_TEXT = 16000
MAX_SECONDS = 60
PAID_DEADLINE = 20
FALLBACK_DEADLINE = 190
INFERENCE = threading.Lock()
HANDLERS = threading.BoundedSemaphore(4)
STOP = threading.Event()


class Unavailable(Exception):
    """Contains a fixed machine code only, never a provider response."""


def parse_audio(content_type, body):
    if len(body) > MAX_BODY or len(content_type) > 256:
        raise ValueError('request size')
    message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart():
        raise ValueError('multipart required')
    fields = {}
    for part in message.iter_parts():
        name = part.get_param('name', header='Content-Disposition')
        if name not in ('file', 'language', 'response_format') or name in fields:
            raise ValueError('unsupported or repeated field')
        if part.get_content_disposition() != 'form-data' or part.is_multipart():
            raise ValueError('invalid part')
        fields[name] = part.get_payload(decode=True)
    if fields.get('language') != b'uk-UA' or fields.get('response_format', b'json') != b'json':
        raise ValueError('language or response format')
    audio = fields.get('file', b'')
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        frames = wav.getnframes()
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('expected mono 16kHz PCM16 WAV')
        if not 0 < frames <= 16000 * MAX_SECONDS or len(wav.readframes(frames)) != frames * 2:
            raise ValueError('duration or truncated audio')
    return audio, frames / 16000


def multipart(audio, primary):
    boundary = 'oak_stt_' + secrets.token_hex(16)
    fields = [('model', 'gpt-transcribe'), ('languages[]', 'uk'),
              ('response_format', 'json'), ('stream', 'false')] if primary else [
              ('language', 'uk-UA'), ('response_format', 'json')]
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                  audio, f'\r\n--{boundary}--\r\n'.encode()])
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def transcript(data):
    if len(data) > MAX_RESPONSE:
        raise Unavailable('malformed_response')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise Unavailable('malformed_response') from None
    if not isinstance(result, dict) or not isinstance(result.get('text'), str) or len(result['text']) > MAX_TEXT:
        raise Unavailable('malformed_response')
    return result['text']  # A valid empty transcript is a completed primary result.


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def openai_worker():
    """Run only as a short-lived trusted child, with audio on stdin."""
    audio = sys.stdin.buffer.read(MAX_BODY + 1)
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key or len(audio) > MAX_BODY:
        return {'ok': False, 'reason': 'not_configured'}
    body, content_type = multipart(audio, True)
    request = urllib.request.Request('https://api.openai.com/v1/audio/transcriptions', data=body,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': content_type}, method='POST')
    try:
        # Default ProxyHandler honors the inherited proxy/no_proxy configuration.
        # SSL's default context validates certificates and honors configured trust.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=PAID_DEADLINE) as response:
            if response.status != 200:
                return {'ok': False, 'reason': 'provider_error'}
            text = transcript(response.read(MAX_RESPONSE + 1))
        return {'ok': True, 'text': text}
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()  # Never retain or print the provider's error body.
        if code in (401, 403):
            reason = 'authentication_or_quota'
        elif code == 429:
            reason = 'rate_or_quota_limit'
        elif 500 <= code <= 599:
            reason = 'provider_unavailable'
        else:
            reason = 'provider_error'
        return {'ok': False, 'reason': reason}
    except Unavailable:
        return {'ok': False, 'reason': 'malformed_response'}
    except Exception:
        return {'ok': False, 'reason': 'network_error'}


class Budget:
    """Private aggregate reservations; uncertain paid outcomes are never refunded."""
    def __init__(self, directory, minutes=600, now=None):
        self.directory = Path(directory)
        self.limit_ms = int(minutes) * 60 * 1000
        self.now = now or (lambda: datetime.datetime.now(ZoneInfo('Europe/Kyiv')))

    def _read(self):
        path = self.directory / 'budget.json'
        if not path.exists() and not path.is_symlink():
            return {'month': self.now().strftime('%Y-%m'), 'reserved_audio_ms': 0, 'requests': 0}
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
                raise ValueError('ledger permissions')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                state = json.load(stream)
        finally:
            os.close(descriptor)
        if (not isinstance(state, dict) or not isinstance(state.get('month'), str)
                or not re.fullmatch(r'\d{4}-\d{2}', state['month'])
                or type(state.get('reserved_audio_ms')) is not int or state['reserved_audio_ms'] < 0
                or type(state.get('requests')) is not int or state['requests'] < 0):
            raise ValueError('invalid ledger')
        month = self.now().strftime('%Y-%m')
        if state['month'] > month:
            raise ValueError('clock moved backwards')
        if state['month'] < month:
            state = {'month': month, 'reserved_audio_ms': 0, 'requests': 0}
        return state

    def _locked(self, milliseconds):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_info = self.directory.stat()
        if self.directory.is_symlink() or directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077:
            raise ValueError('state directory permissions')
        descriptor = os.open(self.directory / 'budget.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        temporary = None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('lock permissions')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state = self._read()
            if milliseconds is None:
                return state['reserved_audio_ms'] < self.limit_ms
            if state['reserved_audio_ms'] + milliseconds > self.limit_ms:
                return False
            state['reserved_audio_ms'] += milliseconds
            state['requests'] += 1
            temporary = self.directory / ('budget.' + secrets.token_hex(8) + '.tmp')
            output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(output, 'w') as stream:
                json.dump(state, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / 'budget.json')
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            os.close(descriptor)

    def available(self):
        try:
            return self._locked(None)
        except (OSError, ValueError):
            return False

    def reserve(self, seconds):
        try:
            return self._locked(math.ceil(seconds * 1000))
        except (OSError, ValueError):
            return False


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, timeout):
        super().__init__('localhost', timeout=timeout)
        self.active_socket = None
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # HTTPConnection clears self.sock on a Connection: close response while
        # its response file still owns the live body socket. Keep that handle
        # available to the absolute-deadline watchdog until body consumption ends.
        self.active_socket = self.sock
        self.sock.settimeout(self.timeout)
        self.sock.connect(FALLBACK_PATH)


class Broker:
    def __init__(self, budget):
        self.budget = budget
        self.counts = {'openai': 0, 'nemotron': 0, 'unavailable': 0}

    def primary_configured(self):
        return bool(os.environ.get('OPENAI_API_KEY', '').strip())

    def paid(self, audio):
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--openai-worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=os.environ.copy())
        try:
            output, _ = process.communicate(audio, timeout=PAID_DEADLINE)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise Unavailable('primary_timeout') from None
        if process.returncode or len(output) > MAX_RESPONSE:
            raise Unavailable('malformed_response')
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise Unavailable('malformed_response') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            allowed = {'not_configured', 'authentication_or_quota', 'rate_or_quota_limit',
                       'provider_unavailable', 'provider_error', 'network_error', 'malformed_response'}
            reason = result.get('reason') if isinstance(result, dict) else None
            raise Unavailable(reason if reason in allowed else 'malformed_response')
        return transcript(json.dumps({'text': result.get('text')}, ensure_ascii=False).encode())

    def local(self, audio=None):
        timeout = 3 if audio is None else FALLBACK_DEADLINE
        connection = UnixConnection(timeout)
        expired = threading.Event()
        def deadline():
            expired.set()
            if connection.active_socket is not None:
                try:
                    connection.active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        timer = threading.Timer(timeout, deadline)
        timer.daemon = True
        timer.start()
        try:
            if audio is None:
                connection.request('GET', '/ready')
            else:
                body, content_type = multipart(audio, False)
                connection.request('POST', '/v1/audio/transcriptions', body,
                                   {'Content-Type': content_type})
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or expired.is_set() or len(data) > MAX_RESPONSE:
                raise Unavailable('fallback_unavailable')
            return True if audio is None else transcript(data)
        except (OSError, http.client.HTTPException):
            raise Unavailable('fallback_unavailable') from None
        finally:
            timer.cancel()
            timer.join()  # No late watchdog can touch the next request's connection.
            connection.close()

    def transcribe(self, audio, seconds):
        reason = 'not_configured'
        if self.primary_configured():
            if self.budget.reserve(seconds):
                try:
                    text = self.paid(audio)
                    self.counts['openai'] += 1
                    return {'text': text, 'provider': 'openai', 'engine': 'gpt-transcribe', 'fallback_reason': None}
                except Unavailable as error:
                    reason = str(error)
                except OSError:
                    reason = 'network_error'
            else:
                reason = 'local_budget_exhausted'
        try:
            text = self.local(audio)
            self.counts['nemotron'] += 1
            return {'text': text, 'provider': 'nemotron', 'engine': 'nemotron-3.5-asr-streaming-0.6b', 'fallback_reason': reason}
        except Unavailable:
            self.counts['unavailable'] += 1
            raise Unavailable('asr_unavailable') from None

    def ready(self):
        configured = self.primary_configured()
        budget = self.budget.available() if configured else False
        if configured and budget:
            return {'ready': True, 'primary_configured': True,
                    'primary_budget_available': True, 'fallback_ready': None,
                    'counts': dict(self.counts)}
        try:
            fallback = self.local()
        except Unavailable:
            fallback = False
        return {'ready': bool(configured and budget or fallback),
                'primary_configured': configured, 'primary_budget_available': budget,
                'fallback_ready': bool(fallback), 'counts': dict(self.counts)}


BROKER = None


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'OakLocalSTT'
    sys_version = ''
    def setup(self):
        self.request.settimeout(5)
        super().setup()
    def log_message(self, *args):
        pass
    def reply(self, status, data):
        self.close_connection = True
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if status == 503:
            self.send_header('Retry-After', '5')
        self.end_headers()
        self.wfile.write(body)
    def error(self, status, code):
        self.reply(status, {'error': {'code': code}})
    def do_GET(self):
        if self.path not in ('/ready', '/health'):
            return self.error(404, 'not_found')
        result = BROKER.ready()
        self.reply(200 if result['ready'] else 503, result)
    def do_POST(self):
        if self.path != '/v1/audio/transcriptions':
            return self.error(404, 'not_found')
        if not INFERENCE.acquire(blocking=False):
            return self.error(503, 'busy')
        body = audio = None
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return self.error(400, 'content_length_required')
            try:
                length = int(self.headers['Content-Length'])
            except (ValueError, TypeError):
                return self.error(400, 'content_length_required')
            if not 0 < length <= MAX_BODY:
                return self.error(413, 'request_too_large')
            end, remaining, chunks = time.monotonic() + 10, length, []
            while remaining:
                seconds = end - time.monotonic()
                if seconds <= 0:
                    raise TimeoutError('body deadline')
                self.connection.settimeout(seconds)
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    raise ValueError('truncated body')
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b''.join(chunks)
            chunks.clear()
            audio, seconds = parse_audio(self.headers.get('Content-Type', ''), body)
            result = BROKER.transcribe(audio, seconds)
            self.connection.settimeout(5)
            self.reply(200, result)
        except (socket.timeout, TimeoutError):
            self.error(504, 'timeout')
        except Unavailable:
            self.error(503, 'asr_unavailable')
        except (ValueError, UnicodeError, wave.Error, EOFError):
            self.error(400, 'invalid_audio_or_language')
        except (OSError, http.client.HTTPException):
            self.error(503, 'asr_unavailable')
        finally:
            body = audio = None
            INFERENCE.release()


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 4
    def process_request(self, request, client_address):
        if not HANDLERS.acquire(blocking=False):
            try:
                request.settimeout(0.1)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            HANDLERS.release()
            raise
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            HANDLERS.release()
    def handle_error(self, *args):
        pass


def main():
    global BROKER
    os.umask(0o077)
    minutes = int(os.environ.get('OAK_STT_MONTHLY_MINUTES', '600'))
    if not 0 <= minutes <= 600:
        raise ValueError('monthly budget out of range')
    BROKER = Broker(Budget(os.environ.get('STATE_DIRECTORY', str(BASE / 'state')), minutes))
    if os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
        raise RuntimeError('systemd socket activation required')
    inherited = socket.socket(fileno=3)
    if inherited.family != socket.AF_UNIX or inherited.getsockname() != SOCKET_PATH:
        raise RuntimeError('unexpected activated socket')
    server = UnixServer(SOCKET_PATH, Handler, bind_and_activate=False)
    server.socket.close()
    server.socket = inherited
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    STOP.wait()
    server.shutdown()
    server.server_close()


if __name__ == '__main__':
    if sys.argv[1:] == ['--openai-worker']:
        try:
            answer = openai_worker()
        except Exception:
            answer = {'ok': False, 'reason': 'network_error'}
        sys.stdout.write(json.dumps(answer, ensure_ascii=False))
    else:
        main()
