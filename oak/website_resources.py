"""Credential-free static downloads under explicit exact-origin policies.

This does not expand navigation, form-action, XHR/fetch or socket permissions.
Responses are fetched independently of the browser cookie jar and fulfilled
locally. Downloaded scripts still execute with the website's DOM privileges.
The old TikTok policy remains an explicit opt-in compatibility setting.
"""

import asyncio
import ipaddress
import json
import re
import socket
import zlib
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector
from aiohttp.abc import AbstractResolver

try:
    import brotli
except ImportError:  # Missing optional runtime support fails closed for br.
    brotli = None


TIKTOK_ORIGIN = 'https://www.tiktok.com'
STATIC_HOSTS = {
    'sf16-website-login.neutral.tiktokcdn-eu.com': frozenset({'script'}),
    'sf16-website-login.neutral.ttwstatic.com': frozenset({'script', 'stylesheet', 'font'}),
}
CONTENT_TYPES = {
    'script': frozenset({'application/javascript', 'text/javascript', 'application/x-javascript',
                         'application/ecmascript', 'text/ecmascript'}),
    'stylesheet': frozenset({'text/css'}),
    'font': frozenset({'font/woff', 'font/woff2', 'font/ttf', 'font/otf',
                       'application/font-woff', 'application/x-font-woff',
                       'application/vnd.ms-fontobject', 'application/octet-stream'}),
}
MAX_POLICY_ORIGINS = 16
MAX_ORIGIN_HOSTS = 8  # Only one origin's hosts are resolved for each lease.
MAX_POLICY_HOSTS = 32
MAX_POLICY_BYTES = 8192  # Leave room in the existing 16 KiB broker config bound.


def _reject():
    raise ValueError('Public website resource unavailable.')


def _decoded_chunks(body, encoding):
    """Decode bounded chunks and require a complete stream before fulfillment.

    Brotli 1.2's buffer limit is a growth threshold, not an exact output cap;
    use a small fixed threshold and enforce resource budgets on every result.
    No whole-body unbounded decompress()/flush() is used.
    """
    if encoding in {'', 'identity'}:
        for offset in range(0, len(body), 65536):
            yield body[offset:offset + 65536]
        return
    if encoding == 'br':
        if brotli is None:
            _reject()
        decoder = brotli.Decompressor()
        pending = body
        while True:
            chunk = decoder.process(pending, 65536)
            if chunk:
                yield chunk
            if decoder.is_finished():
                return
            if not chunk:
                _reject()  # All encoded input was supplied; stream is truncated.
            pending = b''
    elif encoding in {'gzip', 'deflate'}:
        # HTTP deflate is zlib-wrapped, but common older servers send raw
        # deflate. Header inspection chooses once; no network retry occurs.
        wrapped = len(body) >= 2 and body[0] & 15 == 8 and (body[0] * 256 + body[1]) % 31 == 0
        mode = 31 if encoding == 'gzip' else 15 if wrapped else -15
        decoder = zlib.decompressobj(mode)
        pending = body
        while True:
            chunk = decoder.decompress(pending, 65536)
            if chunk:
                yield chunk
            if decoder.eof:
                if not decoder.unused_data:
                    return
                if encoding != 'gzip':
                    _reject()
                pending = decoder.unused_data
                decoder = zlib.decompressobj(mode)  # Concatenated gzip members.
            else:
                pending = decoder.unconsumed_tail
                if not pending and not chunk:
                    _reject()
    else:
        _reject()


def _policy_host(host):
    if (type(host) is not str or not 1 <= len(host) <= 253 or '.' not in host
            or not re.fullmatch(r'[a-z0-9.-]+', host)
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   or label.startswith('xn--') for label in host.split('.'))):
        _reject()
    # Numeric forms also have surprising browser/DNS canonicalizations (127.1,
    # decimal IPv4, octal and hexadecimal). Require a nonnumeric DNS suffix.
    if (not re.search(r'[a-z]', host.rsplit('.', 1)[-1])
            or host.rsplit('.', 1)[-1].startswith('0x')
            or host.endswith(('.localhost', '.local', '.internal'))):
        _reject()
    return host


def validate_static_resources(value, *, tiktok=False):
    """Copy a bounded JSON map; optional legacy grants merge without conflicts.

    Keys are canonical HTTPS origins, then canonical ASCII DNS hostnames. Values
    are explicit resource-type lists. No URL patterns or implicit subdomains.
    DNS is checked and pinned later, only for the selected origin's hosts.
    """
    if type(tiktok) is not bool or type(value) is not dict or len(value) > MAX_POLICY_ORIGINS:
        _reject()
    policies = {}
    for origin, hosts in value.items():
        if type(origin) is not str or not origin.startswith('https://'):
            _reject()
        _policy_host(origin[len('https://'):])
        if type(hosts) is not dict or not 1 <= len(hosts) <= MAX_ORIGIN_HOSTS:
            _reject()
        copied = {}
        for host, kinds in hosts.items():
            _policy_host(host)
            if (host == origin[len('https://'):] or type(kinds) is not list
                    or not 1 <= len(kinds) <= len(CONTENT_TYPES)
                    or any(type(kind) is not str or kind not in CONTENT_TYPES for kind in kinds)
                    or len(set(kinds)) != len(kinds)):
                _reject()
            copied[host] = sorted(kinds)
        policies[origin] = copied
    if tiktok:
        hosts = policies.setdefault(TIKTOK_ORIGIN, {})
        for host, kinds in STATIC_HOSTS.items():
            legacy = sorted(kinds)
            if host in hosts and hosts[host] != legacy:
                _reject()
            hosts[host] = legacy
    if (len(policies) > MAX_POLICY_ORIGINS or any(len(hosts) > MAX_ORIGIN_HOSTS for hosts in policies.values())
            or len({host for hosts in policies.values() for host in hosts}) > MAX_POLICY_HOSTS
            or len(json.dumps(policies).encode('ascii')) > MAX_POLICY_BYTES):
        _reject()
    return policies


def _public(value):
    try:
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and not address.is_reserved
                and not address.is_loopback and not address.is_link_local and not address.is_unspecified)
    except ValueError:
        return False


def _resource_url(url, resource_type, hosts):
    try:
        if (type(url) is not str or not 0 < len(url) <= 4096 or '\\' in url
                or '?' in url or '#' in url or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            _reject()
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or '%' in parsed.netloc
                or resource_type not in hosts.get(parsed.hostname, ())):
            _reject()
        return parsed.hostname
    except (TypeError, ValueError, UnicodeError):
        _reject()


class _PinnedResolver(AbstractResolver):
    def __init__(self, pins):
        self._pins = dict(pins)

    async def resolve(self, host, port=0, family=socket.AF_INET):
        address = self._pins.get(host)
        if port != 443 or address is None or not _public(address):
            _reject()
        return [{'hostname': host, 'host': address, 'port': port,
                 'family': socket.AF_INET6 if ':' in address else socket.AF_INET,
                 'proto': 0, 'flags': socket.AI_NUMERICHOST}]

    async def close(self):
        self._pins.clear()


class StaticWebsiteResources:
    MAX_REQUESTS = 96
    MAX_RESOURCE_BYTES = 4 * 1024 * 1024
    MAX_TOTAL_BYTES = 24 * 1024 * 1024
    MAX_REDIRECTS = 3
    REQUEST_SECONDS = 10

    def __init__(self, origin, *, hosts=None, resolver=None):
        self.origin = origin
        if hosts is None:
            configured = STATIC_HOSTS if origin == TIKTOK_ORIGIN else {}
        else:
            configured = validate_static_resources({origin: hosts})[origin]
        self._hosts = {host: frozenset(kinds) for host, kinds in configured.items()}
        self.active = bool(self._hosts)
        self._resolver = resolver
        self._pins = {}
        self._session = None
        self._closed = False
        self._frozen = False
        self._anonymous_urls = set()
        self._requests = self._bytes = self._wire_bytes = 0
        self._request_quota_rejections = 0
        self._semaphore = asyncio.Semaphore(6)
        self._prepare_lock = asyncio.Lock()

    def diagnostics(self):
        """Fixed counters only; URLs, hosts, headers and bodies stay private."""
        return {'requests': self._requests, 'max_requests': self.MAX_REQUESTS,
                'bytes': self._bytes, 'hosts': len(self._pins),
                'completed_urls': len(self._anonymous_urls), 'frozen': self._frozen,
                'failures': {'request_quota': self._request_quota_rejections}}

    async def prepare(self):
        async with self._prepare_lock:
            await self._prepare()

    async def _prepare(self):
        if not self.active:
            return
        if self._closed:
            _reject()
        if self._session is not None:
            return
        connector = None
        try:
            resolve = self._resolver or asyncio.get_running_loop().getaddrinfo

            async def pin(host):
                records = await asyncio.wait_for(resolve(host, 443, type=socket.SOCK_STREAM), 5)
                addresses = {record[4][0] for record in records}
                if not addresses or not all(_public(address) for address in addresses):
                    _reject()
                return host, sorted(addresses, key=lambda value: (':' in value, value))[0]

            self._pins = dict(await asyncio.gather(*(pin(host) for host in self._hosts)))
            if self._closed:
                _reject()
            connector = TCPConnector(resolver=_PinnedResolver(self._pins), use_dns_cache=False,
                                     ssl=True, limit=6, limit_per_host=6)
            self._session = ClientSession(connector=connector, cookie_jar=DummyCookieJar(),
                trust_env=False, timeout=ClientTimeout(total=self.REQUEST_SECONDS),
                headers={'Accept': '*/*', 'Accept-Encoding': 'identity'},
                skip_auto_headers={'User-Agent', 'Content-Type'}, auto_decompress=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._pins.clear()
            if connector is not None:
                await connector.close()
            _reject()

    def freeze(self):
        """Before any human values, pin paths to completed anonymous downloads.

        Later lazy chunks at new paths fail closed. Otherwise a page could
        encode entered values into a new CDN URL even without query strings.
        """
        self._frozen = True

    async def _authorize(self, url, resource_type):
        return _resource_url(url, resource_type, self._hosts)

    def _completed(self, chain):
        if not self._frozen:
            self._anonymous_urls.update(chain)

    async def _fetch(self, url, method, resource_type):
        async with self._semaphore:
            if self._session is None or self._closed:
                _reject()
            chain = []
            for hop in range(self.MAX_REDIRECTS + 1):
                host = await self._authorize(url, resource_type)
                if host not in self._pins or (self._frozen and url not in self._anonymous_urls):
                    _reject()
                if self._requests >= self.MAX_REQUESTS:
                    self._request_quota_rejections = min(1000000, self._request_quota_rejections + 1)
                    _reject()
                self._requests += 1
                chain.append(url)
                # No browser headers, body, cookies or authorization are used.
                async with self._session.request(method, url, allow_redirects=False, ssl=True) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get('Location', '')
                        if (hop == self.MAX_REDIRECTS or not location or '\\' in location
                                or any(ord(c) < 33 or ord(c) == 127 for c in location)):
                            _reject()
                        url = urljoin(url, location)
                        await self._authorize(url, resource_type)
                        continue
                    content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                    encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
                    if (response.status != 200 or content_type not in CONTENT_TYPES[resource_type]
                            or encoding not in {'', 'identity', 'gzip', 'deflate', 'br'}):
                        _reject()
                    if response.content_length is not None and response.content_length > self.MAX_RESOURCE_BYTES:
                        _reject()
                    encoded = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        self._wire_bytes += len(chunk)
                        if len(encoded) + len(chunk) > self.MAX_RESOURCE_BYTES or self._wire_bytes > self.MAX_TOTAL_BYTES:
                            _reject()
                        encoded.extend(chunk)
                    body = bytearray()
                    for chunk in _decoded_chunks(encoded, 'identity' if method == 'HEAD' else encoding):
                        self._bytes += len(chunk)
                        if len(body) + len(chunk) > self.MAX_RESOURCE_BYTES or self._bytes > self.MAX_TOTAL_BYTES:
                            _reject()
                        body.extend(chunk)
                    headers = {'content-type': content_type, 'cache-control': 'no-store'}
                    allow_origin = response.headers.get('Access-Control-Allow-Origin')
                    if allow_origin in {'*', self.origin}:
                        headers['access-control-allow-origin'] = allow_origin
                    # Set-Cookie, Location, credentials, content encoding and all
                    # other upstream headers never reach the browser context.
                    self._completed(chain)
                    return bytes(body), headers
            _reject()

    async def handle(self, route):
        request = route.request
        try:
            host = urlsplit(request.url).hostname
        except (TypeError, ValueError):
            return False
        if not self.active or host not in self._hosts or request.is_navigation_request():
            return False
        try:
            _resource_url(request.url, request.resource_type, self._hosts)
            if request.method not in {'GET', 'HEAD'} or request.post_data_buffer not in (None, b''):
                _reject()
            body, headers = await asyncio.wait_for(
                self._fetch(request.url, request.method, request.resource_type), self.REQUEST_SECONDS)
            await route.fulfill(status=200, headers=headers, body=body)
        except asyncio.CancelledError:
            raise
        except Exception:
            await route.abort()
        return True

    async def close(self):
        self._closed = True
        self._pins.clear()
        self._anonymous_urls.clear()
        session = self._session
        if session is not None:
            await session.close()
            if self._session is session:
                self._session = None


class _DiscoveredResolver(_PinnedResolver):
    """Only the relay adds independently verified pins to this private map."""

    def __init__(self, pins):
        self._pins = pins


class AnonymousWebsiteResources(StaticWebsiteResources):
    """Discover bounded public frontend dependencies before any private input.

    This is anonymous resource loading, not a credential or navigation grant.
    Its existing parent downloader supplies MIME/size/request/redirect limits,
    strips browser headers and upstream cookies, and freezes completed paths.
    Legacy exact-host policies continue using StaticWebsiteResources unchanged.
    """

    # This relay spans the homepage and subsequent sign-in documents. Keep its
    # byte, host and concurrency bounds while allowing their combined assets.
    MAX_REQUESTS = 512
    MAX_DISCOVERED_HOSTS = MAX_ORIGIN_HOSTS
    DNS_SECONDS = 5

    def __init__(self, origin, *, resolver=None):
        super().__init__(origin, resolver=resolver)
        self._hosts = {}
        self.active = True
        self._host_attempts = set()
        self._discovery_lock = asyncio.Lock()

    async def _prepare(self):
        if self._closed:
            _reject()
        if self._session is not None:
            return
        connector = None
        try:
            connector = TCPConnector(resolver=_DiscoveredResolver(self._pins), use_dns_cache=False,
                ssl=True, limit=6, limit_per_host=6)
            self._session = ClientSession(connector=connector, cookie_jar=DummyCookieJar(),
                trust_env=False, timeout=ClientTimeout(total=self.REQUEST_SECONDS),
                headers={'Accept': '*/*', 'Accept-Encoding': 'identity'},
                skip_auto_headers={'User-Agent', 'Content-Type'}, auto_decompress=False)
        except BaseException:
            if connector is not None:
                await connector.close()
            raise

    @staticmethod
    def _candidate(url, resource_type):
        try:
            host = _policy_host(urlsplit(url).hostname)
            _resource_url(url, resource_type, {host: CONTENT_TYPES})
            return host
        except (TypeError, ValueError, AttributeError):
            _reject()

    async def _authorize(self, url, resource_type):
        host = self._candidate(url, resource_type)
        async with self._discovery_lock:
            if self._closed or (self._frozen and url not in self._anonymous_urls):
                _reject()
            if host in self._pins:
                return _resource_url(url, resource_type, self._hosts)
            if (self._frozen or host in self._host_attempts
                    or len(self._host_attempts) >= self.MAX_DISCOVERED_HOSTS):
                _reject()
            # Failed or cancelled discovery also consumes a host slot. A page
            # cannot cause an unbounded DNS scan or repeated failed resolutions.
            self._host_attempts.add(host)
            resolve = self._resolver or asyncio.get_running_loop().getaddrinfo
            records = await asyncio.wait_for(resolve(host, 443, type=socket.SOCK_STREAM), self.DNS_SECONDS)
            addresses = {record[4][0] for record in records}
            if (self._closed or self._frozen or not addresses
                    or not all(_public(address) for address in addresses)):
                _reject()
            self._pins[host] = sorted(addresses, key=lambda value: (':' in value, value))[0]
            self._hosts[host] = frozenset(CONTENT_TYPES)
            return host

    def _completed(self, chain):
        if self._closed or (self._frozen and any(url not in self._anonymous_urls for url in chain)):
            _reject()
        super()._completed(chain)

    async def handle(self, route):
        request = route.request
        # API calls and navigation remain under the caller's existing policy.
        if request.is_navigation_request() or request.resource_type not in CONTENT_TYPES:
            return False
        try:
            self._candidate(request.url, request.resource_type)
            if request.method not in {'GET', 'HEAD'} or request.post_data_buffer not in (None, b''):
                _reject()
            await self.prepare()
            body, headers = await asyncio.wait_for(
                self._fetch(request.url, request.method, request.resource_type), self.REQUEST_SECONDS)
            await route.fulfill(status=200, headers=headers, body=body)
        except asyncio.CancelledError:
            raise
        except Exception:
            await route.abort()
        return True

    async def close(self):
        self._hosts.clear()
        self._host_attempts.clear()
        await super().close()
