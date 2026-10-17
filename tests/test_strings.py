"""Shared catalogue fallback and Python/browser formatter parity."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from oak.strings import catalogue, format_text, normalize_locale, text


class StringsTests(unittest.TestCase):
    def test_ukrainian_default_variants_and_unknown_locale_fallback(self):
        for locale in (None, 'uk', 'uk-UA', 'uk_UA', 'en-US', 'unknown', 7):
            with self.subTest(locale=locale):
                self.assertEqual(normalize_locale(locale), 'uk')
                self.assertEqual(text('tasks.empty', locale), 'Розклад порожній.')
        self.assertEqual(text('unknown.key'), 'Текст недоступний')
        copy = catalogue('en')
        copy['strings']['tasks.empty'] = 'changed'
        self.assertEqual(text('tasks.empty'), 'Розклад порожній.')
        self.assertEqual(copy['fallback_locale'], 'uk')

    def test_missing_arguments_are_visible_and_user_text_is_not_interpreted(self):
        self.assertEqual(text('tasks.count', count=3), 'Задач у розкладі: 3')
        self.assertEqual(text('tasks.count'), 'Задач у розкладі: {count}')
        self.assertEqual(text('memory.saved', id='<img src=x> {count}'), 'Збережено: <img src=x> {count}')
        self.assertEqual(format_text('{value.__class__} {value}', {'value': 'safe'}), '{value.__class__} safe')
        for value in ([], {}, 1.5, 2**53):
            with self.subTest(value=value), self.assertRaises(ValueError):
                text('tasks.count', count=value)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser formatter parity')
    def test_browser_uses_exact_shared_catalogue_and_scalar_formatting(self):
        source = (Path(__file__).resolve().parents[1] / 'oak/web/locale.js').read_bytes()
        url = 'data:text/javascript;base64,' + base64.b64encode(source).decode()
        cases = [['tasks.count', {'count': 0}], ['tasks.count', {'count': 42}],
                 ['memory.saved', {'id': '<script>{count}</script>'}], ['tasks.count', {}],
                 ['tasks.count', {'count': True}], ['tasks.count', {'count': None}], ['missing', {}]]
        program = '''import fs from 'node:fs';
const [url, catalog, cases] = JSON.parse(fs.readFileSync(0, 'utf8'));
const {createLocale} = await import(url);
const locale = createLocale(catalog);
console.log(JSON.stringify(cases.map(([key,values]) => locale.t(key,values))));'''
        result = subprocess.run(['node', '--input-type=module', '-e', program],
                                input=json.dumps([url, catalogue(), cases]), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [text(key, **values) for key, values in cases])


if __name__ == '__main__':
    unittest.main()
