"""Explicit authentication-host capability and bounded browser-request relay.

The caller enforces initiating frame origin and navigation/WebSocket policy.
The relay pins DNS and checks redirects before following; requests never escape
to Chromium's network stack. API credential bytes exist only during their request.
Scripts are always public, anonymous GET/HEAD downloads: browser credentials and
upstream Set-Cookie are discarded. Authenticated scripts are not supported.
"""

import asyncio
import re
import socket
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector

from .website_resources import CONTENT_TYPES, _PinnedResolver, _policy_host, _public, validate_static_resources


MAX_ORIGINS = 8
MAX_HOSTS_PER_ORIGIN = 4
MAX_TOTAL_HOSTS = 16  # Count bindings, including a host shared by two origins.
ERROR = 'Website authentication host unavailable.'
HOP_HEADERS = {'host', 'content-length', 'connection', 'keep-alive', 'proxy-connection',
               'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
AUTOMATIC_HEADERS = HOP_HEADERS | {'accept-charset', 'accept-encoding', 'access-control-request-headers',
    'access-control-request-method', 'cookie', 'cookie2', 'date', 'dnt', 'expect', 'origin', 'referer',
    'user-agent', 'via'}


class _RelayError(ValueError):
    def __init__(self, code):
        super().__init__(ERROR)
        self.code = code  # Internal literal enum only; never an upstream string.


def _preflight_headers(headers):
    def unsafe(value):
        return any((ord(char) < 32 and char != '\t') or ord(char) == 127 or char in '"():<>?@[\\]{}'
                   for char in value)

    result = []
    for name, value in headers.items():
        if name in AUTOMATIC_HEADERS or name.startswith(('sec-', 'proxy-')):
            continue
        bounded = len(value.encode('utf-8')) <= 128
        simple = name == 'accept' and bounded and not unsafe(value)
        if name in {'accept-language', 'content-language'}:
            simple = bounded and re.fullmatch(r'[0-9A-Za-z *,\-.;=]*', value) is not None
        if name == 'content-type':
            simple = (bounded and not unsafe(value) and value.split(';', 1)[0].strip().lower()
                      in {'application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain'})
        if not simple:
            result.append(name)
    return sorted(result)


def _headers(items, *, response=False):
    """Preserve browser auth/CORS/cookie headers while removing hop metadata."""
    items = list(items)
    if len(items) > 128 or sum(len(key) + len(value) for key, value in items) > 65536:
        raise ValueError(ERROR)
    dropped = set(HOP_HEADERS)
    if response:
        dropped.add('content-encoding')  # aiohttp has decoded the bounded body.
    for key, value in items:
        if key.lower() == 'connection':
            dropped.update(part.strip().lower() for part in value.split(','))
    result = {}
    for key, value in items:
        name = key.lower()
        if name in dropped or name.startswith('proxy-'):
            continue
        if name in result:
            result[name] += ('\n' if name == 'set-cookie' else ', ') + value
        else:
            result[name] = value
    return result


def _origin(value):
    if type(value) is not str or not value.startswith('https://'):
        raise ValueError(ERROR)
    try:
        _policy_host(value[len('https://'):])
    except (TypeError, ValueError):
        raise ValueError(ERROR) from None
    return value


def validate_auth_hosts(value):
    """Copy canonical ``{HTTPS origin: [exact hostname, ...]}`` grants.

    Missing configuration is represented by an empty dictionary. A configured
    origin must have at least one host; duplicates and aliases are rejected.
    All addresses are checked separately when the selected lease prepares.
    """
    if type(value) is not dict or len(value) > MAX_ORIGINS:
        raise ValueError(ERROR)
    result = {}
    total = 0
    for origin, hosts in value.items():
        _origin(origin)
        if (type(hosts) is not list or not 1 <= len(hosts) <= MAX_HOSTS_PER_ORIGIN
                or any(type(host) is not str for host in hosts) or len(set(hosts)) != len(hosts)):
            raise ValueError(ERROR)
        total += len(hosts)
        if total > MAX_TOTAL_HOSTS:
            raise ValueError(ERROR)
        try:
            # Reuse the static policy's canonical ASCII host/origin rules and
            # same-origin exclusion; this does not grant static transport.
            checked = validate_static_resources({origin: {host: ['script'] for host in hosts}})
        except (TypeError, ValueError):
            raise ValueError(ERROR) from None
        result[origin] = sorted(checked[origin])
    return result


class WebsiteAuthHosts:
    DNS_SECONDS = 5
    REQUEST_SECONDS = 10
    MAX_REQUESTS = 128
    MAX_BODY_BYTES = 8 * 1024 * 1024
    MAX_TOTAL_BYTES = 32 * 1024 * 1024
    MAX_REDIRECTS = 3

    def __init__(self, destination, hosts=None, resolver=None):
        self.destination = _origin(destination)
        if hosts is None or (type(hosts) is list and not hosts):
            self._hosts = ()
        else:
            self._hosts = tuple(validate_auth_hosts({destination: hosts})[destination])
        self.active = bool(self._hosts)
        self._resolver = resolver
        self._pins = {}
        self._session = None
        self._requests = self._bytes = 0
        self._fulfilled = self._aborted = self._preflights = 0
        self._statuses, self._failures = {}, {}
        self._semaphore = asyncio.Semaphore(6)
        self._ready = self._closed = False
        self._prepare_lock = asyncio.Lock()

    def diagnostics(self):
        """Private bounded counters; no URL, headers, body or exception text."""
        return {'requests': self._requests, 'transferred_bytes': self._bytes,
                'preflights': self._preflights, 'fulfilled': self._fulfilled, 'aborted': self._aborted,
                'statuses': dict(self._statuses), 'failures': dict(self._failures)}

    def _status(self, status):
        if type(status) is int and 100 <= status <= 599:
            self._statuses[status] = self._statuses.get(status, 0) + 1

    @property
    def resolver_rules(self):
        """Fresh MAP fragments, for the caller's final MAP * ~NOTFOUND rule."""
        if not self._ready or self._closed:
            return []
        return ['MAP ' + host + ' ' + ('[' + address + ']' if ':' in address else address)
                for host, address in sorted(self._pins.items())]

    async def prepare(self):
        async with self._prepare_lock:
            if self._closed:
                raise ValueError(ERROR)
            if self._ready:
                return
            resolve = self._resolver or asyncio.get_running_loop().getaddrinfo

            async def pin(host):
                records = await asyncio.wait_for(resolve(host, 443, type=socket.SOCK_STREAM), self.DNS_SECONDS)
                addresses = {record[4][0] for record in records}
                if not addresses or not all(type(address) is str and _public(address) for address in addresses):
                    raise ValueError(ERROR)
                return host, sorted(addresses, key=lambda address: (':' in address, address))[0]

            tasks = [asyncio.create_task(pin(host)) for host in self._hosts]
            try:
                pins = dict(await asyncio.gather(*tasks))
                if self._closed:
                    raise ValueError(ERROR)
                if pins:
                    connector = TCPConnector(resolver=_PinnedResolver(pins), use_dns_cache=False,
                                             ssl=True, limit=6, limit_per_host=6)
                    try:
                        self._session = ClientSession(connector=connector, cookie_jar=DummyCookieJar(),
                            trust_env=False, timeout=ClientTimeout(total=self.REQUEST_SECONDS),
                            skip_auto_headers={'User-Agent', 'Content-Type', 'Accept-Encoding'},
                            auto_decompress=True)
                    except BaseException:
                        await connector.close()
                        raise
                self._pins = pins
                self._ready = True
            except asyncio.CancelledError:
                self._pins.clear()
                raise
            except Exception:
                self._pins.clear()
                raise ValueError(ERROR) from None
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def allows(self, request):
        """Do not read headers/body/cookies or execute the approved request."""
        if not self._ready or self._closed:
            return False
        try:
            url = request.url
            if (type(url) is not str or not 0 < len(url) <= 4096 or '\\' in url or '#' in url
                    or any(ord(char) < 33 or ord(char) == 127 for char in url)):
                return False
            parsed = urlsplit(url)
            return (parsed.scheme == 'https' and parsed.netloc in self._pins
                    and parsed.username is None and parsed.password is None and parsed.port is None
                    and request.method in {'GET', 'HEAD', 'POST'}
                    and request.resource_type in {'script', 'fetch', 'xhr'}
                    and (request.resource_type != 'script' or request.method in {'GET', 'HEAD'})
                    and request.is_navigation_request() is False)
        except Exception:
            return False

    async def _relay(self, route):
        request = route.request
        async with self._semaphore:
            if not self.allows(request) or self._session is None:
                raise _RelayError('closed_or_unavailable')
            script = request.resource_type == 'script'
            browser_headers = _headers((await request.all_headers()).items())
            if script:
                # Public code is independent of the browser cookie jar. Never
                # forward caller-defined headers, credentials or a request body.
                body = None
                # Intercepted module scripts expose Origin even when Chromium
                # has not yet attached Sec-Fetch-Mode to Playwright headers.
                cors = 'origin' in browser_headers or browser_headers.get('sec-fetch-mode') == 'cors'
                if cors and browser_headers.get('origin') != self.destination:
                    raise _RelayError('initiating_origin_rejected')
                headers = {'accept': '*/*', 'accept-encoding': 'identity'}
                if cors:
                    headers['origin'] = self.destination
            else:
                body = request.post_data_buffer
                if body is not None and (type(body) is not bytes or len(body) > self.MAX_BODY_BYTES):
                    raise _RelayError('request_body_limit')
                headers = browser_headers
                # Browser decoders may support br/zstd absent from aiohttp.
                headers['accept-encoding'] = 'identity'
                cors = True
                if headers.get('origin') != self.destination:
                    raise _RelayError('initiating_origin_rejected')
                preflight = _preflight_headers(headers)
                if preflight:
                    await self._preflight(request.url, request.method, preflight)
            url = request.url
            original_host = urlsplit(url).hostname
            for hop in range(self.MAX_REDIRECTS + 1):
                if self._closed or self._requests >= self.MAX_REQUESTS:
                    raise _RelayError('closed_or_request_limit')
                self._requests += 1  # Reserve before the first network await.
                self._bytes += len(body or b'')
                if self._bytes > self.MAX_TOTAL_BYTES:
                    raise _RelayError('total_byte_limit')
                async with self._session.request(request.method, url, headers=headers, data=body,
                        allow_redirects=False, ssl=True) as response:
                    self._status(response.status)
                    if cors:
                        self._check_cors(response.headers, anonymous=script)
                    if 300 <= response.status < 400 and response.status != 304:
                        # Only anonymous public scripts may redirect. Their
                        # intermediate cookies never enter any cookie jar.
                        if request.resource_type != 'script' or request.method not in {'GET', 'HEAD'}:
                            raise _RelayError('redirect_method_or_type_rejected')
                        if hop == self.MAX_REDIRECTS:
                            raise _RelayError('redirect_limit')
                        location = response.headers.get('Location', '')
                        if (not location or '\\' in location or len(location) > 4096
                                or any(ord(char) < 33 or ord(char) == 127 for char in location)):
                            raise _RelayError('redirect_target_rejected')
                        target = urljoin(url, location)
                        parsed = urlsplit(target)
                        if (parsed.scheme != 'https' or parsed.netloc != original_host or '#' in target
                                or parsed.username is not None or parsed.password is not None
                                or parsed.port is not None or len(target) > 4096):
                            raise _RelayError('redirect_target_rejected')
                        url = target
                        continue
                    if not 200 <= response.status <= 599:
                        raise _RelayError('response_status_rejected')
                    if script:
                        mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                        if response.status != 200 or mime not in CONTENT_TYPES['script']:
                            raise _RelayError('script_content_rejected')
                        # Public-script responses cannot mutate browser state.
                        forwarded = {'content-type': mime, 'cache-control': 'no-store'}
                        allow_origin = response.headers.getall('Access-Control-Allow-Origin', [])
                        if allow_origin in ([self.destination], ['*']):
                            forwarded['access-control-allow-origin'] = allow_origin[0]
                    else:
                        forwarded = _headers(response.headers.items(), response=True)
                    result = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        self._bytes += len(chunk)
                        if len(result) + len(chunk) > self.MAX_BODY_BYTES or self._bytes > self.MAX_TOTAL_BYTES:
                            raise _RelayError('response_byte_limit')
                        result.extend(chunk)
                    if self._closed:
                        raise _RelayError('closed_or_unavailable')
                    await route.fulfill(status=response.status, headers=forwarded,
                                        body=bytes(result))
                    self._fulfilled += 1
                    return
            raise _RelayError('redirect_limit')

    def _check_cors(self, headers, *, preflight=False, anonymous=False):
        # Intercepted Chromium may accept fulfill() despite missing CORS. The
        # relay enforces it explicitly at every hop. Scripts use our explicitly
        # anonymous transport, so one exact or wildcard origin is sufficient.
        # API credentials mode is not observable; keep its stricter check.
        if anonymous:
            if headers.getall('Access-Control-Allow-Origin', []) not in ([self.destination], ['*']):
                raise _RelayError('response_cors_rejected')
            return
        if (headers.getall('Access-Control-Allow-Origin', []) != [self.destination]
                or headers.getall('Access-Control-Allow-Credentials', []) != ['true']):
            raise _RelayError('preflight_cors_rejected' if preflight else 'response_cors_rejected')

    async def _preflight(self, url, method, names):
        if self._closed or self._requests >= self.MAX_REQUESTS:
            raise _RelayError('closed_or_request_limit')
        self._requests += 1
        self._preflights += 1
        headers = {'Origin': self.destination, 'Access-Control-Request-Method': method,
                   'Access-Control-Request-Headers': ', '.join(names)}
        # Explicit headers only: never Cookie, Authorization, body or browser
        # default credentials. A preflight redirect is never followed/fulfilled.
        async with self._session.request('OPTIONS', url, headers=headers, data=None,
                allow_redirects=False, ssl=True) as response:
            self._status(response.status)
            if not 200 <= response.status < 300:
                raise _RelayError('preflight_status_rejected')
            self._check_cors(response.headers, preflight=True)
            methods = {part.strip() for part in response.headers.get('Access-Control-Allow-Methods', '').split(',')}
            allowed = {part.strip().lower() for part in response.headers.get('Access-Control-Allow-Headers', '').split(',')}
            if method not in methods or not set(names).issubset(allowed):
                raise _RelayError('preflight_method_or_headers_rejected')
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                self._bytes += len(chunk)
                if size > self.MAX_BODY_BYTES or self._bytes > self.MAX_TOTAL_BYTES:
                    raise _RelayError('preflight_byte_limit')

    async def handle(self, route):
        """True when consumed; never forwards a redirect or logs secret bytes.

        API requests forward only cookies exposed by actual request headers;
        reconstructing context.cookies() would ignore SameSite. Public scripts
        send no credentials and discard every Set-Cookie, including redirects.
        """
        if not self.allows(route.request):
            return False
        try:
            await asyncio.wait_for(self._relay(route), self.REQUEST_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = error.code if isinstance(error, _RelayError) else 'timeout' if isinstance(error, TimeoutError) else 'transport_error'
            self._failures[code] = self._failures.get(code, 0) + 1
            self._aborted += 1
            await route.abort()
        return True

    async def close(self):
        self._closed = True
        self._ready = False
        self._pins.clear()
        if self._session is not None:
            session = self._session
            self._session = None
            await session.close()
