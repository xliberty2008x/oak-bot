"""Independent known-value masking, without browser or model requests."""

import base64
from copy import deepcopy
import html
import json
import unittest
from urllib.parse import quote, quote_plus

from oak.website_scene import model_context, validate_scene
from oak.website_scene_sanitize import MASK, sanitize_scene
from test_website_scene import scene


class WebsiteSceneSanitizeTests(unittest.TestCase):
    def test_known_raw_normalized_and_encoded_values_are_masked(self):
        secret = 'Key seven/+?'
        forms = [secret, quote(secret, safe=''), quote_plus(secret),
                 base64.b64encode(secret.encode()).decode(),
                 base64.urlsafe_b64encode(secret.encode()).decode().rstrip('='),
                 'Key&#32;seven/+?', 'Key%20seven/+?', 'Key\\u0020seven/+?']
        for encoded in forms:
            with self.subTest(encoded=encoded):
                observed = scene()
                observed['texts'][0]['text'] = 'Check ' + encoded + ' to continue'
                sanitized = sanitize_scene(observed, [], [secret])
                self.assertEqual(sanitized['texts'][0]['text'], 'Check ' + MASK + ' to continue')
        observed = scene()
        observed['actions'][0]['label'] = 'Continue alpha beta'
        self.assertEqual(sanitize_scene(observed, ['alpha \n beta'], [])['actions'][0]['label'], 'Continue ' + MASK)

    def test_html_and_json_escapes_preserve_unrelated_literal_text(self):
        secret = 'k"x<&/R'
        for encoded in (html.escape(secret), json.dumps(secret)[1:-1], 'k&#34;x&lt;&amp;/R'):
            observed = scene()
            observed['texts'][0]['text'] = 'Use &copy; ' + encoded + ' or \\t again'
            result = sanitize_scene(observed, [secret], [])['texts'][0]['text']
            self.assertEqual(result, 'Use &copy; ' + MASK + ' or \\t again')

    def test_unicode_json_surrogate_pairs_and_url_escapes(self):
        secret = 'small\U0001f600value'
        for encoded in (json.dumps(secret, ensure_ascii=True)[1:-1], quote(secret, safe='')):
            observed = scene()
            observed['texts'][0]['text'] = 'Value ' + encoded + ' failed'
            self.assertEqual(sanitize_scene(observed, [secret], [])['texts'][0]['text'], 'Value ' + MASK + ' failed')

    def test_partial_known_value_at_truncated_end_needs_four_characters(self):
        for fragment, expected in (('shortCa', MASK), ('shor', MASK), ('sho', 'sho')):
            observed = scene()
            observed['texts'][0]['text'] = 'Alert ' + fragment
            result = sanitize_scene(observed, ['shortCanary'], [])
            self.assertEqual(result['texts'][0]['text'], 'Alert ' + expected)
        observed = scene()
        observed['texts'][0]['text'] = 'Alert shortCa and continue'
        self.assertEqual(sanitize_scene(observed, ['shortCanary'], [])['texts'][0]['text'], observed['texts'][0]['text'])

    def test_only_textual_scene_properties_change_and_original_remains_owned(self):
        observed = scene()
        observed['controls'][0]['label'] = 'Account shortCanary'
        observed['actions'][0]['label'] = 'Continue shortCanary'
        observed['texts'][0]['text'] = 'Error shortCanary'
        observed['controls'].append({'ref': 'f5', 'type': 'select', 'label': 'Method', 'required': False,
            'form': 'g1', 'order': 8, 'max_length': 64,
            'options': [{'value': 'o1', 'label': 'Option shortCanary'}]})
        observed['history'] = [{'action': 'Choose shortCanary', 'field_types': ['text', 'password']}]
        original = deepcopy(observed)
        result = sanitize_scene(observed, ['shortCanary'], [])
        self.assertEqual(observed, original)
        validate_scene(result)
        self.assertNotIn('shortCanary', json.dumps(model_context(result)))
        self.assertEqual(result['destination'], original['destination'])
        self.assertEqual(result['revision'], original['revision'])
        self.assertEqual(result['controls'][-1]['options'][0]['value'], 'o1')
        self.assertEqual(result['history'][0]['field_types'], ['text', 'password'])
        self.assertEqual(result['history'][0]['action'], 'Choose ' + MASK)

    def test_generic_email_url_and_opaque_filters_apply_without_known_values(self):
        for token in ('account@example.test', 'https://example.test/path?key=abc',
                      'www.example.test/path', 'opaqueToken1234567890123456789'):
            observed = scene()
            observed['texts'][0]['text'] = 'Contact ' + token
            self.assertEqual(sanitize_scene(observed, [], [])['texts'][0]['text'], 'Contact ' + MASK)

    def test_unicode_limits_do_not_split_surrogates_or_expose_known_prefix(self):
        observed = scene()
        observed['controls'][0]['label'] = 'A ' * 79 + 'x\U0001f600'
        result = sanitize_scene(observed, [], [])
        self.assertEqual(len(result['controls'][0]['label'].encode('utf-16-le')) // 2, 159)
        secret = 'secret ' + 'q ' * 200
        observed['texts'][0]['text'] = secret[:240]
        self.assertEqual(sanitize_scene(observed, [secret], [])['texts'][0]['text'], MASK)

    def test_invalid_properties_and_types_are_not_authorized_by_sanitization(self):
        observed = scene()
        observed['controls'][0]['value'] = 'FORBIDDEN_VALUE_CANARY'
        result = sanitize_scene(observed, ['FORBIDDEN_VALUE_CANARY'], [])
        self.assertIn('value', result['controls'][0])
        with self.assertRaises(ValueError):
            validate_scene(result)
        observed = scene()
        observed['texts'][0]['text'] = {'text': 'INVALID_CANARY'}
        with self.assertRaises(ValueError):
            validate_scene(sanitize_scene(observed, [], []))

    def test_bounded_private_inputs_and_scene_failure_never_echo_values(self):
        for redactions in ('SECRET_CANARY', ['SECRET_CANARY'] * 129, [None], ['s' * 4097]):
            with self.assertRaises(ValueError) as error:
                sanitize_scene(scene(), redactions, [])
            self.assertNotIn('CANARY', str(error.exception))
        observed = scene()
        observed['texts'][0]['text'] = 's' * 65536
        with self.assertRaises(ValueError):
            sanitize_scene(observed, [], [])


if __name__ == '__main__':
    unittest.main()
