"""Bounded per-lease HTTPS tunnel and browser navigation interception.

The broker authority registers exact DNS pins. CONNECT is not an origin grant:
encrypted HTTP semantics and redirects remain the browser authority's job.
TLS remains end-to-end; this proxy never decrypts or logs tunneled bytes.
"""
import asyncio
import base64
from dataclasses import dataclass
import hmac
import ipaddress
import re
import secrets
import socket
import time
from urllib.parse import urlsplit


class Denied(ValueError):
    def __init__(self, code='denied'):
        self.code = code
        super().__init__(code)


def public_address(value):
    try:
        if type(value) is not str or '%' in value: return False
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and not address.is_reserved
                and not address.is_loopback and not address.is_link_local and not address.is_unspecified
                and not getattr(address, 'ipv4_mapped', None)
                and not getattr(address, 'sixtofour', None) and not getattr(address, 'teredo', None))
    except (ValueError, TypeError):
        return False


def canonical_host(value):
    if (type(value) is not str or not 1 <= len(value) <= 253 or '.' not in value
            or not re.fullmatch(r'[a-z0-9.-]+', value)
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   or label.startswith('xn--') for label in value.split('.'))
            or not re.search('[a-z]', value.rsplit('.', 1)[-1])
            or value.rsplit('.', 1)[-1].startswith('0x')
            or value.endswith(('.local', '.localhost', '.internal'))):
        raise Denied('invalid_host')
    return value


@dataclass(frozen=True)
class Limits:
    hosts: int = 8
    connections: int = 128
    active: int = 16
    per_host: int = 6
    header_bytes: int = 8192
    header_count: int = 32
    tunnel_bytes: int = 32 * 1024 * 1024
    lease_bytes: int = 128 * 1024 * 1024
    header_seconds: float = 3
    connect_seconds: float = 5
    handshake_seconds: float = 5
    idle_seconds: float = 30
    tunnel_seconds: float = 120
    lease_seconds: float = 300

    def __post_init__(self):
        for key in ('hosts', 'connections', 'active', 'per_host', 'header_bytes',
                    'header_count', 'tunnel_bytes', 'lease_bytes'):
            value = getattr(self, key)
            if type(value) is not int or not 0 < value <= 1024 * 1024 * 1024:
                raise ValueError('Invalid proxy limit.')
        if self.header_bytes > 16384 or self.header_count > 64 or self.hosts > 32 or self.active > 64:
            raise ValueError('Invalid proxy limit.')
        for key in ('header_seconds', 'connect_seconds', 'handshake_seconds',
                    'idle_seconds', 'tunnel_seconds', 'lease_seconds'):
            value = getattr(self, key)
            if type(value) not in (int, float) or not 0 < value <= 900:
                raise ValueError('Invalid proxy timeout.')


async def resolve_public_pin(host, resolver=None):
    """Standalone fixture/admission helper; authority integration resolves once."""
    canonical_host(host)
    lookup = resolver or asyncio.get_running_loop().getaddrinfo
    records = await asyncio.wait_for(lookup(host, 443, type=socket.SOCK_STREAM), 5)
    addresses = {record[4][0] for record in records}
    if not addresses or not all(public_address(address) for address in addresses):
        raise Denied('nonpublic_dns')
    return sorted(addresses, key=lambda value: (':' in value, value))[0]


async def _numeric_upstream(address, port):
    # No upstream hostname or DNS lookup is accepted, including after rebinding.
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.get_running_loop().sock_connect(sock, (address, port))
        return await asyncio.open_connection(sock=sock, limit=32768)
    except BaseException:
        sock.close()
        raise


class LeaseConnectProxy:
    def __init__(self, *, limits=None, upstream_factory=None):
        self.limits = limits or Limits()
        if not isinstance(self.limits, Limits): raise ValueError('Invalid proxy limits.')
        # Injection is a trusted test seam, never configured by model/HTTP input.
        self._upstream_factory = upstream_factory or _numeric_upstream
        self._pins, self._known = {}, {}
        self._frozen = self._closed = False
        self._server = None
        self._created = time.monotonic()
        self._expires = self._created + self.limits.lease_seconds
        self._retained = False
        self._username, self._password = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        self._authorization = b'Basic ' + base64.b64encode((self._username + ':' + self._password).encode())
        self._tasks, self._writers = {}, set()
        self._attempts = self._opened = self._up = self._down = 0
        self._rejections = {}
        self._expiry_task = None
        self._expiry_tasks = set()

    def __repr__(self):
        return '<LeaseConnectProxy credentials=redacted>'

    def _live(self):
        return not self._closed and time.monotonic() < self._expires

    def register(self, host, address):
        """Synchronous authority call; no DNS/client request can register a host."""
        canonical_host(host)
        if type(address) is not str or not public_address(address): raise Denied('nonpublic_pin')
        address = str(ipaddress.ip_address(address))
        if not self._live() or self._frozen: raise Denied('registration_closed')
        if host in self._known and self._known[host] != address: raise Denied('pin_changed')
        if host not in self._known and len(self._known) >= self.limits.hosts: raise Denied('host_limit')
        self._known[host] = self._pins[host] = address

    def retain_only(self, hosts):
        """Revoke before returning; owned active/connecting tunnels are closed."""
        if type(hosts) not in (set, frozenset) or not hosts.issubset(self._pins):
            raise Denied('invalid_retained_hosts')
        self._pins = {host: pin for host, pin in self._pins.items() if host in hosts}
        for task, tunnel in tuple(self._tasks.items()):
            if tunnel['host'] not in hosts:
                for writer in tunnel['writers']: writer.close()
                task.cancel()

    def freeze(self, hosts):
        # The caller passes approved destinations only, not every visited host.
        self._frozen = True
        try: self.retain_only(hosts)
        except Denied:
            self.retain_only(set())
            raise

    def retain_session(self, seconds, hosts):
        """One trusted promotion; preserve quotas and only already pinned hosts.

        Revocation is synchronous. This is not reachable through CONNECT or a
        page request and cannot revive an expired lease or add destinations.
        """
        if (type(seconds) not in (int, float) or not 0 < seconds <= 900
                or type(hosts) not in (set, frozenset) or not hosts
                or not hosts.issubset(self._pins)):
            raise Denied('invalid_retention')
        if not self._live() or self._retained or self._server is None:
            raise Denied('retention_unavailable')
        deadline = time.monotonic() + seconds
        if deadline > self._created + 1200:
            raise Denied('retention_deadline')
        self.freeze(hosts)
        self._retained = True
        self._expires = deadline
        self._schedule_expiry()

    def _schedule_expiry(self):
        if self._expiry_task is not None:
            self._expiry_task.cancel()
        self._expiry_task = asyncio.create_task(self._expire())
        self._expiry_tasks.add(self._expiry_task)
        self._expiry_task.add_done_callback(self._expiry_tasks.discard)

    async def start(self):
        if self._server is not None or not self._live(): raise Denied('listener_unavailable')
        self._server = await asyncio.start_server(self._accept, '127.0.0.1', 0,
            limit=self.limits.header_bytes, backlog=self.limits.active)
        self._schedule_expiry()

    def browser_proxy(self):
        """Pass only to Playwright's proxy object; never argv, JSON logs or repr."""
        if self._server is None or not self._live(): raise Denied('listener_unavailable')
        port = self._server.sockets[0].getsockname()[1]
        return {'server': 'http://127.0.0.1:' + str(port),
                'username': self._username, 'password': self._password}

    def diagnostics(self):
        return {'attempts': self._attempts, 'opened': self._opened, 'active': len(self._tasks),
                'bytes_up': self._up, 'bytes_down': self._down, 'registered': len(self._pins),
                'frozen': self._frozen, 'closed': self._closed, 'rejections': dict(self._rejections)}

    async def _expire(self):
        await asyncio.sleep(max(0, self._expires - time.monotonic()))
        await self.close()

    def _accept(self, reader, writer):
        self._attempts = min(self.limits.connections + 1, self._attempts + 1)
        if not self._live() or self._attempts > self.limits.connections or len(self._tasks) >= self.limits.active:
            writer.close()
            return
        tunnel = {'host': None, 'writers': [writer], 'bytes': 0}
        task = asyncio.create_task(self._serve(reader, writer, tunnel))
        self._tasks[task] = tunnel
        self._writers.add(writer)

    async def _reply(self, writer, status):
        names = {200: 'Connection Established', 400: 'Bad Request', 403: 'Forbidden',
                 405: 'Method Not Allowed', 407: 'Proxy Authentication Required', 429: 'Too Many Requests'}
        header = 'HTTP/1.1 ' + str(status) + ' ' + names[status] + '\r\n'
        if status == 407: header += 'Proxy-Authenticate: Basic realm="Oak lease"\r\n'
        if status != 200: header += 'Connection: close\r\nContent-Length: 0\r\n'
        writer.write((header + '\r\n').encode('ascii'))
        await asyncio.wait_for(writer.drain(), self.limits.header_seconds)

    def _parse(self, data):
        if len(data) > self.limits.header_bytes: raise Denied('header_limit')
        lines = data[:-4].split(b'\r\n')
        if len(lines) < 2 or len(lines) - 1 > self.limits.header_count: raise Denied('header_limit')
        parts = lines[0].split(b' ')
        if len(parts) != 3 or parts[2] != b'HTTP/1.1': raise Denied('invalid_request')
        if parts[0] != b'CONNECT': raise Denied('method_forbidden')
        try: target = parts[1].decode('ascii')
        except UnicodeError: raise Denied('invalid_request') from None
        if not target.endswith(':443'): raise Denied('authority_forbidden')
        host = canonical_host(target[:-4])
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(b':')
            name = name.lower()
            if (not sep or not re.fullmatch(rb'[a-z0-9!#$%&\'*+.^_`|~-]+', name)
                    or name in headers or any(c < 32 and c != 9 or c > 126 for c in value)):
                raise Denied('invalid_header')
            headers[name] = value.strip(b' \t')
        if any(name in headers for name in (b'content-length', b'transfer-encoding', b'cookie', b'authorization')):
            raise Denied('body_or_credentials_forbidden')
        if headers.get(b'host') != parts[1]: raise Denied('host_mismatch')
        if not hmac.compare_digest(headers.get(b'proxy-authorization', b''), self._authorization):
            raise Denied('proxy_authentication')
        return host

    def _allowed(self, host, address):
        return self._live() and self._pins.get(host) == address

    async def _serve(self, reader, writer, tunnel):
        established = False
        try:
            data = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), self.limits.header_seconds)
            host = self._parse(data)
            address = self._pins.get(host)
            if address is None or not self._allowed(host, address): raise Denied('host_forbidden')
            if sum(v['host'] == host for v in self._tasks.values()) >= self.limits.per_host:
                raise Denied('host_connections')
            tunnel['host'] = host
            upstream_reader, upstream_writer = await asyncio.wait_for(
                self._upstream_factory(address, 443), self.limits.connect_seconds)
            tunnel['writers'].append(upstream_writer); self._writers.add(upstream_writer)
            if not self._allowed(host, address): raise Denied('host_revoked')
            await self._reply(writer, 200); established = True; self._opened += 1
            async with asyncio.timeout(min(self.limits.tunnel_seconds, max(.001, self._expires-time.monotonic()))):
                first = await asyncio.wait_for(reader.readexactly(6), self.limits.handshake_seconds)
                length = int.from_bytes(first[3:5], 'big')
                if not (first[0] == 22 and first[1] == 3 and 1 <= first[2] <= 3
                        and 4 <= length <= 18432 and first[5] == 1): raise Denied('tls_required')
                await self._forward(upstream_writer, first, tunnel, address, True)
                a = asyncio.create_task(self._pipe(reader, upstream_writer, tunnel, address, True))
                b = asyncio.create_task(self._pipe(upstream_reader, writer, tunnel, address, False))
                try:
                    done, pending = await asyncio.wait((a, b), return_when=asyncio.FIRST_COMPLETED)
                    for task in done: task.result()
                finally:
                    for task in (a, b): task.cancel()
                    await asyncio.gather(a, b, return_exceptions=True)
        except Denied as error:
            self._rejections[error.code] = self._rejections.get(error.code, 0) + 1
            if not established:
                status = 407 if error.code == 'proxy_authentication' else 405 if error.code == 'method_forbidden' else 403
                try: await self._reply(writer, status)
                except Exception: pass
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
            self._rejections['transport_or_bound'] = self._rejections.get('transport_or_bound', 0) + 1
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let an injected transport exception print host/header data.
            self._rejections['transport_failure'] = self._rejections.get('transport_failure', 0) + 1
        finally:
            for stream in tunnel['writers']: stream.close()
            for stream in tunnel['writers']:
                try: await asyncio.wait_for(stream.wait_closed(), 1)
                except Exception: pass
                self._writers.discard(stream)
            self._tasks.pop(asyncio.current_task(), None)

    async def _forward(self, writer, data, tunnel, address, upstream):
        if not self._allowed(tunnel['host'], address): raise Denied('host_revoked')
        if tunnel['bytes'] + len(data) > self.limits.tunnel_bytes or self._up + self._down + len(data) > self.limits.lease_bytes:
            raise Denied('byte_limit')
        # No await between budget validation and reservation across both pumps.
        tunnel['bytes'] += len(data)
        if upstream: self._up += len(data)
        else: self._down += len(data)
        writer.write(data)
        await asyncio.wait_for(writer.drain(), self.limits.idle_seconds)

    async def _pipe(self, reader, writer, tunnel, address, upstream):
        while True:
            data = await asyncio.wait_for(reader.read(16384), self.limits.idle_seconds)
            if not data: return
            await self._forward(writer, data, tunnel, address, upstream)

    async def close(self):
        self._closed = True; self._frozen = True; self._pins.clear()
        if self._server:
            # Python 3.12's Server.wait_closed waits for active transports too.
            # Stop accepting first, then revoke/close our owned tunnels before
            # awaiting the listener, rather than waiting for the idle deadline.
            self._server.close()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for writer in tuple(self._writers): writer.close()
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()
        expiry_tasks = [task for task in self._expiry_tasks if task is not current]
        for task in expiry_tasks: task.cancel()
        if expiry_tasks: await asyncio.gather(*expiry_tasks, return_exceptions=True)
        self._username = self._password = ''; self._authorization = b''


@dataclass(frozen=True, repr=False)
class NavigationRequest:
    """Broker-only metadata; URL can contain OAuth state and must not be logged.

    No headers, cookies or request body cross the authorization callback.
    `redirected_from` is a CDP request identifier, never a URL.
    """
    url: str
    method: str
    request_id: str
    redirected_from: str | None
    frame_id: str
    resource_type: str
    main_frame: bool


class WebsiteNavigationTransport:
    """Keep browser TLS/cookies intact; the caller alone authorizes origins.

    Start before browser launch and pass browser_proxy()/browser_args() to
    Playwright. Attach after context routing and page creation, before goto.
    The async authorize(request) callback must validate the current owner grant,
    navigation/dispatch transaction and ALL DNS answers, returning its immutable
    public numeric pin. It may await private approval without holding a lease
    lock needed by that approval. CONNECT never creates a grant.

    Existing Playwright routing still controls initial subresources and the
    credential-free/static and explicit auth-host relays. Redirected subresources
    are conservatively aborted here because Playwright skips their route hooks.
    Popups/other pages remain the caller's denied scope. No TLS overrides,
    request reconstruction, header override, automatic retry or body replay.
    """
    MAX_REQUESTS = 2048
    MAX_PENDING = 64
    MAX_URL_BYTES = 16384  # Private protocol data, never diagnostic output.
    CDP_CLEANUP_SECONDS = 1

    def __init__(self, authorize, *, on_block=None, limits=None,
                 approval_seconds=120, upstream_factory=None):
        if (not callable(authorize) or (on_block is not None and not callable(on_block))
                or type(approval_seconds) not in (int, float) or not 0 < approval_seconds <= 120):
            raise ValueError('Invalid navigation transport configuration.')
        self._authorize, self._on_block = authorize, on_block
        self._approval_seconds = approval_seconds
        self._proxy = LeaseConnectProxy(limits=limits, upstream_factory=upstream_factory)
        self._cdp = self._page = self._settings = None
        self._main_frame = None
        self._closed = False
        self._tasks, self._pending = set(), {}
        self._requests = self._continued = self._denied = 0
        self._reasons = {}
        self._pause_listener = self._auth_listener = None

    def __repr__(self):
        return '<WebsiteNavigationTransport private_state=redacted>'

    async def start(self):
        if self._closed:
            raise Denied('transport_closed')
        await self._proxy.start()
        self._settings = self._proxy.browser_proxy()

    def browser_proxy(self):
        """Private Playwright launch option only; never persist or log this."""
        return dict(self._proxy.browser_proxy())

    @staticmethod
    def browser_args():
        return ['--disable-quic', '--dns-prefetch-disable',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                '--proxy-bypass-list=<-loopback>']

    async def attach(self, context, page):
        if self._closed or self._cdp is not None or self._settings is None:
            raise Denied('transport_unavailable')
        self._page = page
        self._cdp = await context.new_cdp_session(page)
        try:
            tree = await self._cdp.send('Page.getFrameTree')
            self._main_frame = tree['frameTree']['frame']['id']
            self._pause_listener, self._auth_listener = self._paused, self._auth_required
            self._cdp.on('Fetch.requestPaused', self._pause_listener)
            self._cdp.on('Fetch.authRequired', self._auth_listener)
            await self._cdp.send('Fetch.enable', {
                'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}],
                'handleAuthRequests': True})
        except BaseException:
            await self.close()
            raise

    def freeze(self, approved_hosts):
        """Close admission and revoke every unapproved tunnel synchronously."""
        self._proxy.freeze(approved_hosts)

    def retain_session(self, seconds, hosts):
        """Trusted connected-session promotion after root-origin validation."""
        if self._closed:
            raise Denied('transport_closed')
        self._proxy.retain_session(seconds, hosts)

    def diagnostics(self):
        return {'requests': self._requests, 'continued': self._continued,
                'denied': self._denied, 'pending': len(self._pending),
                'closed': self._closed, 'reasons': dict(self._reasons),
                'proxy': self._proxy.diagnostics()}

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _paused(self, event):
        if self._closed:
            return  # close() revokes the proxy, then disables/releases Fetch.
        # Project immediately: held approval work must not retain the CDP event's
        # headers/body. Only trusted broker code receives the private URL.
        request = event.get('request', {})
        item = NavigationRequest(request.get('url'), request.get('method'),
            event.get('requestId'), event.get('redirectedRequestId'),
            event.get('frameId'), event.get('resourceType'),
            event.get('frameId') == self._main_frame)
        self._requests = min(self.MAX_REQUESTS + 1, self._requests + 1)
        if self._requests > self.MAX_REQUESTS or len(self._pending) >= self.MAX_PENDING:
            # Shut down the finite transport rather than accumulate unbounded
            # failRequest tasks from a page producing a request flood.
            self._record_block('request_limit', navigation=True)
            if not self._closed:
                self._closed = True
                self._spawn(self.close())
            return
        if item.request_id in self._pending:
            self._record_block('duplicate_request')
            return
        self._pending[item.request_id] = item
        self._spawn(self._handle(item))

    @staticmethod
    def _host(item):
        if (type(item.url) is not str or not 0 < len(item.url.encode('utf-8')) <= WebsiteNavigationTransport.MAX_URL_BYTES
                or '\\' in item.url or any(ord(c) < 33 or ord(c) == 127 for c in item.url)
                or type(item.method) is not str or not re.fullmatch('[A-Z]{1,16}', item.method)
                or any(type(v) is not str or not 0 < len(v) <= 256
                       for v in (item.request_id, item.frame_id, item.resource_type))
                or (item.redirected_from is not None and
                    (type(item.redirected_from) is not str or not 0 < len(item.redirected_from) <= 256))):
            raise Denied('invalid_request')
        parsed = urlsplit(item.url)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment):
            raise Denied('invalid_request')
        return canonical_host(parsed.hostname)

    def _record_block(self, reason, *, navigation=False):
        self._denied = min(self.MAX_REQUESTS + 1, self._denied + 1)
        self._reasons[reason] = min(self.MAX_REQUESTS + 1, self._reasons.get(reason, 0) + 1)
        if navigation and self._on_block is not None:
            try:
                self._on_block(reason)
            except Exception:
                pass  # Callback text must not escape through an event task.

    async def _fail(self, request_id):
        try:
            if self._cdp is not None:
                await asyncio.wait_for(self._cdp.send('Fetch.failRequest', {
                    'requestId': request_id, 'errorReason': 'BlockedByClient'}), self.CDP_CLEANUP_SECONDS)
        except Exception:
            pass

    async def _handle(self, item):
        reason = None
        try:
            host = self._host(item)
            if self._closed or not self._proxy._live():
                raise Denied('transport_closed')
            if item.resource_type == 'Document':
                if not item.main_frame:
                    raise Denied('frame_navigation_denied')
                if item.method not in {'GET', 'HEAD', 'POST'}:
                    raise Denied('navigation_method_denied')
                if item.redirected_from is not None and item.method not in {'GET', 'HEAD'}:
                    raise Denied('redirect_body_replay_denied')
                timeout = min(self._approval_seconds, self._proxy._expires - time.monotonic())
                if timeout <= 0:
                    raise Denied('transport_closed')
                try:
                    # Keep the callback in this task so a lease finishing from
                    # inside it can close() without awaiting/cancelling itself.
                    async with asyncio.timeout(timeout):
                        address = await self._authorize(item)
                except asyncio.TimeoutError:
                    raise Denied('approval_timeout') from None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise Denied('origin_not_approved') from None
                if self._closed or not self._proxy._live():
                    raise Denied('transport_closed')
                # Frozen approved pins remain usable. Registration may never
                # add or change a pin after the private-input freeze.
                if self._proxy._pins.get(host) != address:
                    self._proxy.register(host, address)
            elif item.redirected_from is not None:
                raise Denied('resource_redirect_unsupported')
            # Other initial subresources still pass through the caller's
            # Playwright route. Unregistered CONNECT targets fail independently.
            if self._closed:
                raise Denied('transport_closed')
            await self._cdp.send('Fetch.continueRequest', {'requestId': item.request_id})
            self._continued += 1
        except Denied as error:
            reason = error.code
        except asyncio.CancelledError:
            await self._fail(item.request_id)
            raise
        except Exception:
            reason = 'interception_failed'
        finally:
            if reason is not None:
                self._record_block(reason, navigation=item.resource_type == 'Document' and item.main_frame)
                await self._fail(item.request_id)
            self._pending.pop(item.request_id, None)

    def _auth_required(self, event):
        if self._closed:
            return
        challenge = event.get('authChallenge', {})
        allowed = (not self._closed and self._settings is not None
                   and challenge.get('source') == 'Proxy'
                   and challenge.get('origin') == self._settings['server']
                   and challenge.get('scheme') == 'basic' and challenge.get('realm') == 'Oak lease')
        request_id = event.get('requestId')
        if type(request_id) is not str or len(request_id) > 256:
            return
        if len(self._tasks) >= self.MAX_PENDING:
            self._record_block('request_limit', navigation=True)
            if not self._closed:
                self._closed = True
                self._spawn(self.close())
            return
        self._spawn(self._handle_auth(request_id, allowed))

    async def _handle_auth(self, request_id, allowed):
        reply = {'response': 'CancelAuth'}
        if allowed and not self._closed and self._settings is not None:
            reply = {'response': 'ProvideCredentials', 'username': self._settings['username'],
                     'password': self._settings['password']}
        try:
            await self._cdp.send('Fetch.continueWithAuth', {'requestId': request_id, 'authChallengeResponse': reply})
        except Exception:
            pass

    async def close(self):
        self._closed = True
        # Revoke transport before releasing any browser pause. Even a detached
        # CDP session cannot reopen a direct/proxy network path during cleanup.
        await self._proxy.close()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._cdp is not None:
            await asyncio.gather(*(self._fail(request_id) for request_id in tuple(self._pending)),
                                 return_exceptions=True)
            self._pending.clear()
            cdp, self._cdp = self._cdp, None
            for event, listener in [('Fetch.requestPaused', self._pause_listener), ('Fetch.authRequired', self._auth_listener)]:
                if listener is not None:
                    cdp.remove_listener(event, listener)
            # Chromium may leave detach waiting behind active Fetch interception.
            # Disable only after proxy revocation; released requests cannot reach
            # any registered origin. The browser owner subsequently closes it.
            try:
                await asyncio.wait_for(cdp.send('Fetch.disable'), self.CDP_CLEANUP_SECONDS)
            except Exception:
                pass
            try:
                await asyncio.wait_for(cdp.detach(), self.CDP_CLEANUP_SECONDS)
            except Exception:
                pass
        self._settings = None
