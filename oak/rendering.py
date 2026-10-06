"""Shared safe Markdown AST and bounded Telegram representations.

Source Markdown stays with the caller. AST values are text, never trusted HTML.
Clients must build their DOM using whitelisted elements and textContent.
"""

from dataclasses import dataclass
import html
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_block.table import escapedSplit


MAX_SOURCE_CHARS = 200_000
MAX_AST_NODES = 4000
MAX_AST_DEPTH = 16
MAX_URL_BYTES = 2048
CLASSIC_LIMIT = 4096
MAX_CLASSIC_HTML_BYTES = 16_384
MAX_RICH_TEXT_BYTES = 32_768
MAX_RICH_HTML_BYTES = 64_000
MAX_RICH_BLOCKS = 300
MAX_RICH_DEPTH = 12
MAX_RICH_COLUMNS = 20


@dataclass(frozen=True)
class RenderedChunk:
    html: str
    plain: str
    rich: bool = False


def utf16_length(text):
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def safe_url(value):
    """Only bounded HTTP(S) links without credentials or control characters."""
    if not isinstance(value, str) or not value or len(value.encode('utf-8', errors='replace')) > MAX_URL_BYTES:
        return None
    if '\\' in value or any(char.isspace() or unicodedata.category(char).startswith('C') for char in value):
        return None
    decoded = unquote(value)
    if any(unicodedata.category(char).startswith('C') for char in decoded):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        if '%' in parsed.hostname or parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
    except ValueError:
        return None
    return value


def _clean(text):
    if not isinstance(text, str):
        raise TypeError('Markdown must be text.')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return ''.join('\ufffd' if 0xD800 <= ord(char) <= 0xDFFF or
                   ord(char) < 32 and char not in '\n\t' else char for char in text)


def _text(value):
    return {'type': 'text', 'value': value}


def _literal(source):
    return {'type': 'document', 'literal': True,
            'children': [{'type': 'paragraph', 'children': [_text(source)]}] if source else []}


class _LiteralFallback(Exception):
    pass


def _walk(node, depth=0):
    yield node, depth
    for child in node.get('children', []):
        yield from _walk(child, depth + 1)


def _value(node):
    if node['type'] == 'break':
        return '\n'
    return node.get('value', '') + ''.join(_value(child) for child in node.get('children', []))


def _convert(tokens):
    result = []
    stack = [result]
    kinds = {'paragraph': 'paragraph', 'heading': 'heading', 'strong': 'strong',
             'em': 'emphasis', 's': 'strike', 'link': 'link', 'blockquote': 'blockquote',
             'bullet_list': 'list', 'ordered_list': 'list', 'list_item': 'list_item',
             'table': 'table', 'tr': 'table_row', 'th': 'table_cell', 'td': 'table_cell'}
    for token in tokens:
        if token.level >= MAX_AST_DEPTH:
            # The parser's own higher limit can discard tail text. Never accept
            # a token stream that approaches that limit, even if no leaf remains.
            raise _LiteralFallback
        if token.type in {'thead_open', 'thead_close', 'tbody_open', 'tbody_close'}:
            continue
        if token.type == 'inline':
            stack[-1].extend(_convert(token.children or []))
        elif token.nesting == 1:
            name = token.type.removesuffix('_open')
            if name not in kinds:
                raise _LiteralFallback
            node = {'type': kinds[name], 'children': []}
            if name == 'heading':
                node['level'] = int(token.tag[1])
            elif name in {'bullet_list', 'ordered_list'}:
                node.update(ordered=name == 'ordered_list', start=int(token.attrGet('start') or 1))
            elif name == 'link':
                node['url'] = safe_url(token.attrGet('href'))
            elif name in {'th', 'td'}:
                node['header'] = name == 'th'
                align = (token.attrGet('style') or '').removeprefix('text-align:')
                if align in {'left', 'center', 'right'}:
                    node['align'] = align
            stack[-1].append(node)
            stack.append(node['children'])
        elif token.nesting == -1:
            if len(stack) == 1:
                raise _LiteralFallback
            stack.pop()
        elif token.type == 'text':
            stack[-1].append(_text(token.content))
        elif token.type in {'softbreak', 'hardbreak'}:
            stack[-1].append({'type': 'break'})
        elif token.type == 'code_inline':
            stack[-1].append({'type': 'code', 'value': token.content})
        elif token.type in {'fence', 'code_block'}:
            language = token.info.split()[0] if token.info.strip() else ''
            node = {'type': 'code_block', 'value': token.content}
            if re.fullmatch(r'[A-Za-z0-9_+#.\-]{1,32}', language):
                node['language'] = language
            stack[-1].append(node)
        elif token.type == 'image':
            # Never expose src to either adapter or fetch remote image content.
            alt = ''.join(_value(n) for n in _convert(token.children or []))
            stack[-1].append(_text(alt or token.content))
        elif token.type == 'hr':
            stack[-1].append({'type': 'paragraph', 'children': [_text('────')]})
        else:
            raise _LiteralFallback
    if len(stack) != 1:
        raise _LiteralFallback
    return result


def _check_table_content(tokens, lines):
    """Markdown tables otherwise silently discard surplus cells in a row."""
    for index, token in enumerate(tokens):
        if token.type != 'table_open' or not token.map:
            continue
        start, end = token.map
        columns = 0
        for header_token in tokens[index + 1:]:
            if header_token.type == 'thead_close':
                break
            columns += header_token.type == 'th_open'
        rows = []
        for line in lines[start:end]:
            line = re.sub(r'^\s*(?:>\s*)*', '', line).strip()
            cells = escapedSplit(line)
            if cells and not cells[0]:
                cells.pop(0)
            if cells and not cells[-1]:
                cells.pop()
            rows.append(cells)
        if rows and any(len(row) > columns for row in rows[2:]):
            raise _LiteralFallback


def parse_markdown(text):
    """Return a JSON-safe whitelist AST; limits preserve all source literally."""
    source = _clean(text)
    if len(source) > MAX_SOURCE_CHARS:
        return _literal(source)
    parser = MarkdownIt('commonmark', {'html': False, 'linkify': False,
                                      'typographer': False, 'maxNesting': 32})
    parser.enable(['table', 'strikethrough'])
    try:
        tokens = parser.parse(source)
        _check_table_content(tokens, source.splitlines())
        document = {'type': 'document', 'literal': False, 'children': _convert(tokens)}
        nodes = list(_walk(document))
        if len(nodes) > MAX_AST_NODES or any(depth > MAX_AST_DEPTH for _, depth in nodes):
            raise _LiteralFallback
        # Invalid links retain their visible labels with no executable URL.
        for node, _ in nodes:
            if node['type'] == 'link' and not node['url']:
                node.pop('url')
                node['type'] = 'paragraph_inline'
        # Flatten these transparent wrappers rather than exposing a new DOM type.
        def flatten(parent):
            children = []
            for child in parent.get('children', []):
                flatten(child)
                children.extend(child['children'] if child['type'] == 'paragraph_inline' else [child])
            if 'children' in parent:
                parent['children'] = children
        flatten(document)
        return document
    except (_LiteralFallback, RecursionError):
        return _literal(source)


def _tag(name, attribute=''):
    return (f'<{name}{attribute}>', f'</{name}>')


def _style(styles, tag):
    return styles if tag in styles else (*styles, tag)


def _classic(document):
    spans = []

    def add(value, styles=()):
        if value:
            if spans and spans[-1][1] == styles:
                spans[-1] = (spans[-1][0] + value, styles)
            else:
                spans.append((value, styles))

    def visit(node, styles=(), indent=0):
        kind, children = node['type'], node.get('children', [])
        if kind == 'text':
            add(node['value'], styles)
        elif kind == 'break':
            add('\n', styles)
        elif kind in {'code', 'code_block'}:
            language = node.get('language', '')
            tag = _tag('code') if kind == 'code' else (
                '<pre><code' + (f' class="language-{language}"' if language else '') + '>', '</code></pre>')
            add(node['value'], (tag,))
            if kind == 'code_block':
                add('\n\n')
        elif kind == 'table':
            rows = [row['children'] for row in children]
            headers = rows[0] if rows and all(cell.get('header') for cell in rows[0]) else []
            records = rows[1:] if headers else rows
            if not records:
                for index, cell in enumerate(headers):
                    if index:
                        add(' / ')
                    for child in cell['children']:
                        visit(child, styles)
            for row in records:
                for index, cell in enumerate(row):
                    if headers and index < len(headers):
                        for child in headers[index]['children']:
                            visit(child, styles)
                        add(': ', styles)
                    value_styles = _style(styles, _tag('b')) if index == 0 else styles
                    for child in cell['children']:
                        visit(child, value_styles)
                    add('\n')
                add('\n')
            add('\n')
        elif kind == 'list':
            for index, child in enumerate(children):
                prefix = f'{node.get("start", 1) + index}. ' if node.get('ordered') else '• '
                add('  ' * indent + prefix)
                visit(child, styles, indent + 1)
            add('\n')
        else:
            if kind in {'strong', 'heading', 'emphasis', 'strike', 'blockquote', 'link'}:
                names = {'strong': 'b', 'heading': 'b', 'emphasis': 'i', 'strike': 's', 'blockquote': 'blockquote', 'link': 'a'}
                attr = ' href="' + html.escape(node['url'], quote=True) + '"' if kind == 'link' else ''
                styles = _style(styles, _tag(names[kind], attr))
            for child in children:
                visit(child, styles, indent)
            if kind in {'paragraph', 'heading', 'blockquote'}:
                add('\n' if indent else '\n\n')
            elif kind == 'list_item' and (not spans or not spans[-1][0].endswith('\n')):
                add('\n')

    if document['literal']:
        return [(_value(document), ())]
    visit(document)
    while spans and not spans[-1][0].rstrip('\n'):
        spans.pop()
    if spans and not spans[-1][1]:
        spans[-1] = (spans[-1][0].rstrip('\n'), spans[-1][1])
    return spans


def _chunks(spans):
    result, parts, plain = [], [], []
    tags, units, byte_count = (), 0, 0

    def flush():
        nonlocal parts, plain, tags, units, byte_count
        if plain:
            result.append(RenderedChunk(''.join(parts) + ''.join(tag[1] for tag in reversed(tags)), ''.join(plain)))
        parts, plain, tags, units, byte_count = [], [], (), 0, 0

    for value, desired_tags in spans:
        for char in value:
            escaped = html.escape(char, quote=False)
            cost = 2 if ord(char) > 0xFFFF else 1
            desired = desired_tags
            while True:
                common = 0
                while common < min(len(tags), len(desired)) and tags[common] == desired[common]:
                    common += 1
                transition = ''.join(tag[1] for tag in reversed(tags[common:])) + ''.join(tag[0] for tag in desired[common:])
                endings = ''.join(tag[1] for tag in reversed(desired))
                added = len((transition + escaped).encode('utf-8'))
                fits = units + cost <= CLASSIC_LIMIT and byte_count + added + len(endings.encode('utf-8')) <= MAX_CLASSIC_HTML_BYTES
                if fits:
                    parts.extend((transition, escaped))
                    plain.append(char)
                    tags, units, byte_count = desired, units + cost, byte_count + added
                    break
                if plain:
                    flush()
                else:
                    # A pathological style cannot consume a whole chunk budget.
                    desired = ()
    flush()
    return result


def _rich(node):
    kind = node['type']
    if kind in {'text', 'code', 'code_block'}:
        value = html.escape(node['value'], quote=False)
        if kind == 'text':
            return value
        if kind == 'code':
            return '<code>' + value + '</code>'
        language = node.get('language', '')
        return '<pre><code' + (f' class="language-{language}"' if language else '') + '>' + value + '</code></pre>\n'
    if kind == 'break':
        return '<br/>'
    content = ''.join(_rich(child) for child in node.get('children', []))
    if kind == 'document':
        return content
    names = {'paragraph': 'p', 'heading': 'h' + str(node.get('level', 1)),
             'strong': 'b', 'emphasis': 'i', 'strike': 's', 'link': 'a',
             'list': 'ol' if node.get('ordered') else 'ul', 'list_item': 'li',
             'blockquote': 'blockquote', 'table': 'table', 'table_row': 'tr',
             'table_cell': 'th' if node.get('header') else 'td'}
    attr = ''
    if kind == 'link':
        attr = ' href="' + html.escape(node['url'], quote=True) + '"'
    elif kind == 'table':
        attr = ' bordered striped compact'
    elif kind == 'list' and node.get('ordered') and node.get('start', 1) != 1:
        attr = f' start="{node["start"]}"'
    elif kind == 'table_cell' and node.get('align'):
        attr = ' align="' + node['align'] + '"'
    suffix = '\t' if kind == 'table_cell' else ('\n' if kind in {'paragraph', 'heading', 'list', 'list_item', 'blockquote', 'table', 'table_row'} else '')
    return '<' + names[kind] + attr + '>' + content + '</' + names[kind] + '>' + suffix


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')


def render_markdown(text, rich=False):
    """One guarded rich chunk, or balanced classic HTML chunks with exact text."""
    document = parse_markdown(text)
    if rich and not document['literal'] and document['children']:
        nodes = list(_walk(document))
        blocks = sum(node['type'] in {'paragraph', 'heading', 'code_block', 'list', 'list_item', 'blockquote', 'table', 'table_row'} for node, _ in nodes)
        if (blocks <= MAX_RICH_BLOCKS and all(depth <= MAX_RICH_DEPTH for _, depth in nodes)
                and all(len(node.get('children', [])) <= MAX_RICH_COLUMNS for node, _ in nodes if node['type'] == 'table_row')):
            markup = _rich(document)
            parser = _PlainHTML()
            parser.feed(markup)
            plain = ''.join(parser.parts)
            if plain.strip() and len(plain.encode('utf-8')) <= MAX_RICH_TEXT_BYTES and len(markup.encode('utf-8')) <= MAX_RICH_HTML_BYTES:
                return [RenderedChunk(markup, plain, True)]
    return _chunks(_classic(document))
