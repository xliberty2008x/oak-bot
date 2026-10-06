from html.parser import HTMLParser
import json
import unittest

from oak.rendering import (CLASSIC_LIMIT, MAX_CLASSIC_HTML_BYTES, MAX_RICH_HTML_BYTES,
                           MAX_RICH_TEXT_BYTES, MAX_SOURCE_CHARS, parse_markdown,
                           render_markdown, safe_url, utf16_length)


class ParsedHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.text = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.text.append('\n')
        else:
            self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.tags and self.tags.pop() == tag, 'unbalanced generated HTML'

    def handle_data(self, value):
        self.text.append(value)


class RenderingTests(unittest.TestCase):
    def verify_chunks(self, chunks):
        for chunk in chunks:
            parsed = ParsedHTML()
            parsed.feed(chunk.html)
            self.assertFalse(parsed.tags)
            self.assertEqual(''.join(parsed.text), chunk.plain)
            self.assertTrue(chunk.plain)
            if chunk.rich:
                self.assertLessEqual(len(chunk.plain.encode()), MAX_RICH_TEXT_BYTES)
                self.assertLessEqual(len(chunk.html.encode()), MAX_RICH_HTML_BYTES)
            else:
                self.assertLessEqual(utf16_length(chunk.plain), CLASSIC_LIMIT)
                self.assertLessEqual(len(chunk.html.encode()), MAX_CLASSIC_HTML_BYTES)

    def test_shared_ast_and_native_structure(self):
        source = '# Heading\n\n- **One**\n- [Two](https://example.com)\n\n| Name | Value |\n|---|---|\n| Oak | **42** |'
        ast = parse_markdown(source)
        self.assertFalse(ast['literal'])
        self.assertEqual(ast['children'][0]['type'], 'heading')
        self.assertEqual(ast['children'][1]['type'], 'list')
        self.assertEqual(ast['children'][2]['type'], 'table')
        json.dumps(ast)
        chunks = render_markdown(source, rich=True)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].rich)
        for tag in ('<h1>', '<ul>', '<table bordered striped compact>', '<th>', '<b>42</b>'):
            self.assertIn(tag, chunks[0].html)
        self.verify_chunks(chunks)

    def test_classic_table_becomes_labelled_records(self):
        source = '| Plan | Price | Link |\n|---|---|---|\n| Basic | **10** | [Site](https://example.com?a=1&b=2) |'
        chunk = render_markdown(source)[0]
        self.assertNotIn('|', chunk.plain)
        self.assertIn('Plan: Basic', chunk.plain)
        self.assertIn('Price: 10', chunk.plain)
        self.assertIn('Link: Site', chunk.plain)
        self.assertIn('<b>10</b>', chunk.html)
        self.assertIn('href="https://example.com?a=1&amp;b=2"', chunk.html)
        self.verify_chunks([chunk])

    def test_unsafe_html_links_and_images_are_only_text(self):
        source = '<img src=x onerror=alert(1)>\n\n[Private](https://user:pass@example.com/) ![cat **picture**](https://evil.example/image.png) [Bad](javascript:alert(1))'
        ast = parse_markdown(source)
        self.assertNotIn('https://evil.example/image.png', json.dumps(ast))
        for chunk in render_markdown(source, rich=True):
            self.assertNotIn('<img', chunk.html)
            self.assertNotIn('href=', chunk.html)
            self.assertIn('&lt;img', chunk.html)
            self.assertIn('cat picture', chunk.plain)
        self.verify_chunks(render_markdown(source))

    def test_url_validation_checks_controls_credentials_and_bounds(self):
        for url in ('javascript:alert(1)', 'https://user@example.com', 'https://u:p@example.com', 'https://@example.com',
                    'https://example.com/\n', 'https://example.com/%0a',
                    'https://example.com/\u202e', 'https://example.com\\evil',
                    'https://example.com:999999', 'https://example.com/' + 'x' * 3000):
            with self.subTest(url=url[:45]):
                self.assertIsNone(safe_url(url))
        self.assertEqual(safe_url('https://example.com/a%20b?q=1&x=2'), 'https://example.com/a%20b?q=1&x=2')

    def test_long_emoji_entities_and_links_split_without_loss(self):
        body = ('🙂 & < > Привіт ' * 900)
        source = '**[' + body + '](https://example.com/' + 'a&' * 500 + ')**'
        chunks = render_markdown(source)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(c.plain for c in chunks), body)
        self.verify_chunks(chunks)

    def test_code_fence_and_unfinished_markdown_keep_content(self):
        source = '```python\nprint("<unsafe>&🙂")\n\n'
        chunks = render_markdown(source)
        self.assertEqual(''.join(c.plain for c in chunks), 'print("<unsafe>&🙂")\n\n')
        self.assertIn('<pre><code class="language-python">', chunks[0].html)
        self.verify_chunks(chunks)
        unfinished = render_markdown('**still typing [a link](https://example.com')
        self.assertIn('still typing', unfinished[0].plain)
        self.verify_chunks(unfinished)

    def test_parser_depth_guard_preserves_tail_literally(self):
        source = '> ' * 100 + 'KEEP_EVERY_CHARACTER\n'
        self.assertTrue(parse_markdown(source)['literal'])
        chunks = render_markdown(source, rich=True)
        self.assertTrue(all(not c.rich for c in chunks))
        self.assertEqual(''.join(c.plain for c in chunks), source)
        self.verify_chunks(chunks)

    def test_node_and_source_limits_preserve_entire_source(self):
        for source in ('*word* ' * 2000, 'x' * (MAX_SOURCE_CHARS + 1) + 'TAIL'):
            self.assertTrue(parse_markdown(source)['literal'])
            chunks = render_markdown(source, rich=True)
            self.assertEqual(''.join(c.plain for c in chunks), source)
            self.verify_chunks(chunks)

    def test_rich_overflow_becomes_classic_without_dropping_text(self):
        source = '\n\n'.join('Paragraph ' + str(i) for i in range(350))
        chunks = render_markdown(source, rich=True)
        self.assertTrue(all(not c.rich for c in chunks))
        self.assertIn('Paragraph 349', ''.join(c.plain for c in chunks))
        self.verify_chunks(chunks)

    def test_wide_and_overfull_tables_preserve_cells(self):
        source = '| ' + ' | '.join(f'H{i}' for i in range(21)) + ' |\n'
        source += '|' + '|'.join('---' for _ in range(21)) + '|\n'
        source += '|' + '|'.join(f'V{i}' for i in range(21)) + '|'
        chunks = render_markdown(source, rich=True)
        self.assertTrue(all(not c.rich for c in chunks))
        self.assertIn('H20: V20', ''.join(c.plain for c in chunks))
        overflow = '| A | B |\n|---|---|\n| x | y | NEVER_DROP |'
        self.assertTrue(parse_markdown(overflow)['literal'])
        self.assertEqual(''.join(c.plain for c in render_markdown(overflow)), overflow)
        nested = '- | A | B |\n  |---|---|\n  | x | y | NEVER_DROP |'
        self.assertTrue(parse_markdown(nested)['literal'])
        self.assertEqual(''.join(c.plain for c in render_markdown(nested)), nested)

    def test_empty_and_unsafe_code_language(self):
        self.assertEqual(render_markdown(''), [])
        chunks = render_markdown('```x\"onclick=evil\nbody\n```', rich=True)
        self.assertNotIn('class=', chunks[0].html)
        self.verify_chunks(chunks)


if __name__ == '__main__':
    unittest.main()
