import importlib.util
from pathlib import Path
import tempfile
import unittest

from oak.media import MediaTools
from oak.research import _Readable, public_url


class ToolTests(unittest.TestCase):
    def test_workspace_boundary_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside.wav'
            outside.write_bytes(b'audio')
            tools = MediaTools(root / 'workspace')
            (tools.workspace / 'linked.wav').symlink_to(outside)
            for path in (outside, 'linked.wav'):
                with self.assertRaises(ValueError):
                    tools._input(path)

    def test_page_reader_omits_executable_content(self):
        page = _Readable()
        page.feed('<title>Oak</title><script>secret()</script><style>hidden</style>'
                  '<p>Привіт &amp; світ</p><a class="result__a" href="https://example.com">Example</a>')
        self.assertNotIn('secret', ''.join(page.parts))
        self.assertNotIn('hidden', ''.join(page.parts))
        self.assertIn('Привіт & світ', ''.join(page.parts))
        self.assertEqual(page.links, [{'url': 'https://example.com', 'title': 'Example'}])

    def test_research_rejects_local_and_credential_urls(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'https://[::1]/',
                    'https://user:secret@example.com/'):
            with self.assertRaises(ValueError):
                public_url(url)

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is optional')
    def test_titlecard_is_real_unicode_png(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            tools = MediaTools(folder)
            output = tools.generate_image('Дуб — нова історія', subtitle='Oak', width=640, height=360)
            with Image.open(output) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.size, (640, 360))
                self.assertGreater(len(image.getcolors(640 * 360)), 2)


if __name__ == '__main__':
    unittest.main()
