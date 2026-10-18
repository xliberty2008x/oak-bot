"""Anonymous, read-only Hugging Face Space discovery and capability evidence.

These observations never establish generation access, remaining quota or price.
No account token, upload, prediction, Space creation or purchase is supported.
"""

from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


HUB = 'https://huggingface.co'
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10
SPACE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,79}\Z')
ENDPOINT = re.compile(r'/[A-Za-z_][A-Za-z0-9_/-]{0,119}\Z')


class ObservationError(ValueError):
    """Public fixed error codes; never expose HTTP bodies or exception URLs."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ObservationError('redirect_refused')


def _get_json(url):
    # urllib does not load Hugging Face tokens, .netrc credentials or cookies.
    request = Request(url, headers={'Accept': 'application/json',
                                   'User-Agent': 'Oak-public-space-observation/1'}, method='GET')
    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ObservationError('response_too_large')
        return json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except ObservationError:
        raise
    except HTTPError as exc:
        raise ObservationError('access_denied' if exc.code in (401, 403) else 'http_error') from None
    except (URLError, OSError, TimeoutError):
        raise ObservationError('network_unavailable') from None
    except (ValueError, UnicodeError, RecursionError):
        raise ObservationError('invalid_json') from None


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _space_id(value):
    if not isinstance(value, str) or not SPACE_ID.fullmatch(value):
        raise ValueError('Supply a public Hugging Face owner/space identifier.')
    return value


def _host(value):
    if not isinstance(value, str):
        raise ObservationError('untrusted_host')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.hf\.space', parsed.netloc)):
        raise ObservationError('untrusted_host')
    return value.rstrip('/')


def _public(info, expected=None):
    if not isinstance(info, dict) or info.get('private') is not False:
        raise ObservationError('public_access_unverified')
    if info.get('gated') not in (None, False) or info.get('disabled') is True:
        raise ObservationError('public_access_denied')
    identifier = _space_id(info.get('id'))
    if expected is not None and identifier != expected:
        raise ObservationError('identity_mismatch')
    return identifier


class Spaces:
    """GET-only public observations, with an injectable transport for local tests."""

    def __init__(self, get_json=None):
        self._get = get_json or _get_json

    def search(self, query, limit=5):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise ValueError('Search text must contain 1–128 characters.')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('Search limit must be 1–10.')
        result = {'observed_at': _stamp(), 'status': 'unavailable', 'spaces': [],
                  'generation_available': 'unknown'}
        try:
            data = self._get(HUB + '/api/spaces?' + urlencode({'search': query.strip(), 'limit': limit}))
            if not isinstance(data, list):
                raise ObservationError('invalid_schema')
            for row in data[:limit]:
                try:
                    identifier = _public(row)
                except ValueError:
                    continue
                result['spaces'].append({'id': identifier, 'url': HUB + '/spaces/' + identifier,
                                         'sdk': row.get('sdk') if row.get('sdk') in ('gradio', 'docker', 'static') else 'unknown'})
            result['status'] = 'observed'
        except ObservationError as exc:
            result['error'] = str(exc)
        return result

    def inspect(self, space_id):
        identifier = _space_id(space_id)
        result = {'id': identifier, 'observed_at': _stamp(), 'status': 'unavailable',
                  'source': HUB + '/spaces/' + identifier,
                  'image_to_video': 'unknown', 'generation_available': 'unknown',
                  'cost': {'status': 'unknown', 'amount': None},
                  'quota': {'status': 'unknown', 'remaining_seconds': None},
                  'queue': {'status': 'unknown', 'position': None},
                  'endpoints': [],
                  'limitations': ['Public schema is capability evidence, not a successful generation.',
                                  'No generation, upload, account access or billing check was performed.']}
        try:
            info = self._get(HUB + '/api/spaces/' + identifier)
            _public(info, identifier)
            sha = info.get('sha')
            result['revision'] = sha if isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) else None
            runtime = info.get('runtime') or {}
            if not isinstance(runtime, dict):
                raise ObservationError('invalid_schema')
            stage = runtime.get('stage')
            result['runtime'] = stage if stage in ('RUNNING', 'SLEEPING', 'BUILDING', 'PAUSED', 'STOPPED',
                                                   'RUNTIME_ERROR', 'BUILD_ERROR', 'RUNNING_BUILDING') else 'unknown'
            if info.get('sdk') != 'gradio':
                raise ObservationError('unsupported_sdk')
            if stage != 'RUNNING':
                raise ObservationError('runtime_not_running')
            host = _host(info.get('host'))
            config = self._get(host + '/config')
            if not isinstance(config, dict):
                raise ObservationError('invalid_schema')
            if config.get('auth_required') is True:
                raise ObservationError('public_access_denied')
            prefix = config.get('api_prefix')
            # Only the currently observed public Gradio API contract is supported.
            if prefix != '/gradio_api':
                raise ObservationError('unsupported_api_schema')
            result['queue']['enabled'] = config.get('enable_queue') if type(config.get('enable_queue')) is bool else None
            schema = self._get(host + prefix + '/info')
            endpoints = schema.get('named_endpoints') if isinstance(schema, dict) else None
            if not isinstance(endpoints, dict) or len(endpoints) > 64:
                raise ObservationError('invalid_schema')
            for name, endpoint in endpoints.items():
                if not isinstance(name, str) or not ENDPOINT.fullmatch(name) or not isinstance(endpoint, dict):
                    raise ObservationError('invalid_schema')
                inputs, outputs = endpoint.get('parameters'), endpoint.get('returns')
                if not isinstance(inputs, list) or not isinstance(outputs, list) or len(inputs) > 64 or len(outputs) > 64:
                    raise ObservationError('invalid_schema')
                if not all(isinstance(item, dict) for item in inputs + outputs):
                    raise ObservationError('invalid_schema')
                if any(item.get('component') == 'Image' for item in inputs) and any(item.get('component') == 'Video' for item in outputs):
                    names = [item.get('parameter_name') for item in inputs]
                    if not all(isinstance(item, str) and NAME.fullmatch(item) for item in names):
                        raise ObservationError('invalid_schema')
                    result['endpoints'].append({'name': name, 'parameters': names,
                                                'input': 'Image', 'output': 'Video'})
            result.update(status='observed', image_to_video='declared' if result['endpoints'] else 'not_declared')
        except (ObservationError, ValueError) as exc:
            result['endpoints'] = []
            result['error'] = str(exc) if isinstance(exc, ObservationError) else 'invalid_schema'
        return result
