"""Bounded public-page reading, search and public YouTube metadata."""

from html.parser import HTMLParser
import ipaddress
import json
import socket
import subprocess
import sys
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree


def public_url(url):
    """Reject local resources and credentials, including after redirects."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise ValueError('Provide a public HTTP or HTTPS URL without credentials.') from None
    return url


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Readable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.links = [], [], []
        self.hidden = 0
        self.in_title = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == 'title':
            self.in_title = True
        if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'section', 'tr'}:
            self.parts.append('\n')
        if tag == 'a':
            self.anchor = {'url': attrs.get('href', ''), 'title': ''}

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'a' and self.anchor:
            item = self.anchor
            item['title'] = ' '.join(item['title'].split())
            self.links.append(item)
            self.anchor = None

    def handle_data(self, value):
        if self.hidden:
            return
        if self.in_title:
            self.title.append(value)
        self.parts.append(value)
        if self.anchor:
            self.anchor['title'] += value


class ResearchTools:
    def __init__(self, search_url=None):
        # Optional SearXNG endpoint with JSON output enabled, without credentials.
        self.search_url = search_url

    @staticmethod
    def _get(url):
        public_url(url)
        request = Request(url, headers={'User-Agent': 'Oak/0.1 (public research)',
                                       'Accept': 'text/html,text/plain,application/json',
                                       'Accept-Encoding': 'identity'})
        try:
            with build_opener(_PublicRedirect()).open(request, timeout=25) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise RuntimeError('Page exceeds the 2 MiB reading limit.')
                mime = response.headers.get_content_type()
                if mime not in {'text/html', 'text/plain', 'application/json', 'application/xhtml+xml',
                                'text/xml', 'application/xml', 'application/rss+xml'}:
                    raise RuntimeError('This URL is not a supported text or HTML page.')
                charset = response.headers.get_content_charset() or 'utf-8'
                return raw.decode(charset, errors='replace'), response.geturl(), mime
        except (OSError, LookupError):
            raise RuntimeError('Public page could not be retrieved.') from None

    def fetch(self, url):
        source, final_url, mime = self._get(url)
        if mime in {'text/html', 'application/xhtml+xml'}:
            parser = _Readable()
            parser.feed(source)
            text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
            links = []
            for link in parser.links:
                target = urljoin(final_url, link['url'])
                if urlsplit(target).scheme in {'http', 'https'}:
                    links.append({'title': link['title'], 'url': target})
            title = ' '.join(''.join(parser.title).split())
        else:
            title, text, links = '', source, []
        return {'url': final_url, 'title': title, 'text': text[:30000],
                'truncated': len(text) > 30000, 'links': links[:50]}

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError('Search query must contain 1–1000 characters.')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('Request 1–10 search results.')
        if self.search_url:
            separator = '&' if '?' in self.search_url else '?'
            source, _, _ = self._get(self.search_url + separator + urlencode({'q': query, 'format': 'json'}))
            try:
                data = json.loads(source)
                return [{'title': item.get('title', ''), 'url': item['url'],
                         'snippet': item.get('content', '')}
                        for item in data.get('results', [])[:limit]
                        if urlsplit(item.get('url', '')).scheme in {'http', 'https'}]
            except (ValueError, TypeError, KeyError):
                raise RuntimeError('Configured search service returned invalid results.') from None
        # Public Bing RSS search: no account, API key or scraped browser state.
        source, _, _ = self._get('https://www.bing.com/search?' + urlencode({'q': query, 'format': 'rss'}))
        try:
            root = ElementTree.fromstring(source)
        except ElementTree.ParseError:
            raise RuntimeError('Public search did not return an RSS result feed.') from None
        results = [{'title': item.findtext('title', ''), 'url': item.findtext('link', ''),
                    'snippet': item.findtext('description', '')}
                   for item in root.findall('./channel/item')
                   if urlsplit(item.findtext('link', '')).scheme in {'http', 'https'}]
        if not results:
            raise RuntimeError('Public search returned no readable results; try another query or configure SearXNG.')
        return results[:limit]

    def youtube(self, url):
        public_url(url)
        if urlsplit(url).hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
            raise ValueError('Provide a public YouTube URL.')
        try:
            result = subprocess.run([sys.executable, '-m', 'yt_dlp', '--ignore-config',
                '--skip-download', '--dump-single-json', '--no-warnings', '--no-playlist',
                '--flat-playlist', '--playlist-end', '10', '--socket-timeout', '20',
                '--retries', '0', '--', url], capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError('YouTube metadata unavailable; install yt-dlp or try another public URL.')
            data = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise RuntimeError('YouTube metadata could not be retrieved.') from None
        keys = ('id', 'title', 'description', 'webpage_url', 'channel', 'channel_url',
                'duration', 'view_count', 'like_count', 'upload_date', 'thumbnail')
        filtered = {key: data[key] for key in keys if key in data}
        filtered['description'] = (filtered.get('description') or '')[:12000]
        if 'entries' in data:
            filtered['entries'] = [{key: item[key] for key in ('id', 'title', 'url', 'duration') if key in item}
                                   for item in data['entries'][:10] if isinstance(item, dict)]
        return filtered
