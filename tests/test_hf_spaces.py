import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from oak.hf_spaces import HUB, MAX_BYTES, ObservationError, Spaces, _get_json, _NoRedirect


SPACE = 'illustrator/local-animation'
HOST = 'https://illustrator-local-animation.hf.space'


class SpacesTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.metadata = {'id': SPACE, 'private': False, 'gated': False, 'sdk': 'gradio',
                         'sha': 'a' * 40, 'host': HOST, 'runtime': {'stage': 'RUNNING'}}
        self.config = {'api_prefix': '/gradio_api', 'enable_queue': True}
        self.schema = {'named_endpoints': {'/generate_video': {
            'parameters': [{'parameter_name': 'input_image', 'component': 'Image'},
                           {'parameter_name': 'prompt', 'component': 'Textbox'}],
            'returns': [{'component': 'Video'}]}}}
        self.data = {HUB + '/api/spaces/' + SPACE: self.metadata,
                     HOST + '/config': self.config, HOST + '/gradio_api/info': self.schema}
        self.client = Spaces(self.get)

    def get(self, url):
        self.calls.append(url)
        return copy.deepcopy(self.data[url])

    def test_public_search_works_without_connector_and_excludes_private_rows(self):
        url = HUB + '/api/spaces?search=blink+%26+hair&limit=3'
        self.data[url] = [self.metadata, dict(self.metadata, private=True),
                          dict(self.metadata, id='../../credentials')]
        result = self.client.search('blink & hair', 3)
        self.assertEqual(self.calls, [url])
        self.assertEqual(result['status'], 'observed')
        self.assertEqual([row['id'] for row in result['spaces']], [SPACE])
        self.assertEqual(result['generation_available'], 'unknown')

    def test_running_image_video_schema_is_not_free_generation_or_quota_evidence(self):
        self.metadata['price'] = 0  # Unspecified fields are not authoritative evidence.
        self.metadata['quota'] = 1000
        result = self.client.inspect(SPACE)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['image_to_video'], 'declared')
        self.assertEqual(result['revision'], 'a' * 40)
        self.assertEqual(result['endpoints'][0]['parameters'], ['input_image', 'prompt'])
        self.assertEqual(result['generation_available'], 'unknown')
        self.assertEqual(result['cost'], {'status': 'unknown', 'amount': None})
        self.assertEqual(result['quota'], {'status': 'unknown', 'remaining_seconds': None})
        self.assertEqual(result['queue'], {'status': 'unknown', 'position': None, 'enabled': True})
        self.assertEqual(self.calls, list(self.data))

    def test_video_name_without_image_input_is_not_animation_capability(self):
        self.schema['named_endpoints']['/generate_video']['parameters'][0]['component'] = 'Textbox'
        self.assertEqual(self.client.inspect(SPACE)['image_to_video'], 'not_declared')

    def test_private_gated_disabled_or_wrong_identity_never_contacts_space(self):
        for changes in [{'private': True}, {'private': None}, {'gated': 'auto'},
                        {'disabled': True}, {'id': 'different/space'}]:
            with self.subTest(changes=changes):
                original = self.metadata.copy()
                self.metadata.update(changes)
                self.calls.clear()
                result = self.client.inspect(SPACE)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(len(self.calls), 1)
                self.metadata.clear()
                self.metadata.update(original)

    def test_nonrunning_space_is_not_woken_or_assumed_available(self):
        for stage in ['SLEEPING', 'PAUSED', 'BUILD_ERROR', 'UNKNOWN_NEW_STAGE']:
            self.metadata['runtime']['stage'] = stage
            self.calls.clear()
            result = self.client.inspect(SPACE)
            self.assertEqual(result['error'], 'runtime_not_running')
            self.assertEqual(result['image_to_video'], 'unknown')
            self.assertEqual(len(self.calls), 1)

    def test_nonpublic_or_credential_hosts_are_rejected_before_request(self):
        for host in ['http://valid.hf.space', 'https://127.0.0.1', 'https://localhost',
                     'https://valid.hf.space.evil.test', 'https://evil.test',
                     'https://user:password@valid.hf.space', 'https://valid.hf.space:443',
                     'https://valid.hf.space/path', 'https://valid.hf.space?token=CANARY',
                     'https://valid.hf.space#CANARY', 'https://[::1]', None]:
            with self.subTest(host=host):
                self.metadata['host'] = host
                self.calls.clear()
                result = self.client.inspect(SPACE)
                self.assertEqual(result['error'], 'untrusted_host')
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn('CANARY', json.dumps(result))

    def test_authenticated_page_and_unobserved_api_prefix_are_denied(self):
        for config, error in [({'auth_required': True, 'api_prefix': '/gradio_api'}, 'public_access_denied'),
                              ({'api_prefix': 'https://evil.test'}, 'unsupported_api_schema'),
                              ({}, 'unsupported_api_schema')]:
            self.data[HOST + '/config'] = config
            self.calls.clear()
            self.assertEqual(self.client.inspect(SPACE)['error'], error)
            self.assertEqual(len(self.calls), 2)

    def test_bad_schema_cannot_leave_partial_positive_capability(self):
        self.schema['named_endpoints']['/later_broken'] = {'parameters': None, 'returns': []}
        result = self.client.inspect(SPACE)
        self.assertEqual(result['error'], 'invalid_schema')
        self.assertEqual(result['image_to_video'], 'unknown')
        self.assertEqual(result['endpoints'], [])

    def test_failure_is_honest_and_search_input_is_bounded_before_network(self):
        client = Spaces(lambda _: (_ for _ in ()).throw(ObservationError('network_unavailable')))
        self.assertEqual(client.search('image to video')['status'], 'unavailable')
        self.assertEqual(client.inspect(SPACE)['generation_available'], 'unknown')
        for query, limit in [('', 1), ('x' * 129, 1), ('valid', True), ('valid', 0), ('valid', 11)]:
            with self.assertRaises(ValueError):
                self.client.search(query, limit)
        for identifier in ['../space', '/owner/space', 'owner/space?token=x', 'owner/space/subpath']:
            with self.assertRaises(ValueError):
                self.client.inspect(identifier)
        self.assertEqual(self.calls, [])


class AnonymousTransportTests(unittest.TestCase):
    def test_transport_only_uses_get_without_cookies_or_authorization(self):
        with patch('oak.hf_spaces.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value = io.BytesIO(b'{"ok":true}')
            self.assertEqual(_get_json(HUB + '/api/spaces?search=image'), {'ok': True})
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(set(name.lower() for name, _ in request.header_items()), {'accept', 'user-agent'})
            self.assertIsInstance(build.call_args.args[0], _NoRedirect)

    def test_redirect_never_reaches_new_host_or_private_endpoint(self):
        with self.assertRaisesRegex(ObservationError, 'redirect_refused'):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/private')

    def test_bounded_body_invalid_json_and_http_errors_are_sanitized(self):
        for body, code in [(b'x' * (MAX_BYTES + 1), 'response_too_large'),
                           (b'{broken CANARY', 'invalid_json'), (b'{"quota":NaN}', 'invalid_json')]:
            with self.subTest(code=code), patch('oak.hf_spaces.build_opener') as build:
                build.return_value.open.return_value.__enter__.return_value = io.BytesIO(body)
                with self.assertRaisesRegex(ObservationError, '^' + code + '$'):
                    _get_json(HUB)
        with patch('oak.hf_spaces.build_opener') as build:
            build.return_value.open.side_effect = HTTPError(HUB, 403, 'CANARY', {}, io.BytesIO(b'CANARY'))
            with self.assertRaisesRegex(ObservationError, '^access_denied$'):
                _get_json(HUB)


if __name__ == '__main__':
    unittest.main()
