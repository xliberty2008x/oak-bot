"""Bounded browser actions on a broker-owned, task-scoped website session."""

import base64
import re
import secrets
from urllib.parse import parse_qsl, unquote, urlsplit


APPLICATIONS = {
    'google': frozenset({'www.google.com', 'www.youtube.com', 'studio.youtube.com',
                         'mail.google.com', 'drive.google.com', 'docs.google.com',
                         'calendar.google.com'}),
    'instagram': frozenset({'www.instagram.com'}),
}
RESOURCES = {
    'google': ('google.com', 'googleapis.com', 'gstatic.com', 'googleusercontent.com',
               'youtube.com', 'ytimg.com', 'googlevideo.com', 'ggpht.com'),
    'instagram': ('instagram.com', 'cdninstagram.com', 'fbcdn.net'),
}
PRIVATE_PATH = re.compile(r'/(?:accounts?|challenge|oauth|signin|signout|login|logout|'
                          r'password|security|two_factor|backup_codes)(?:/|$)', re.I)
PRIVATE_QUERY = re.compile(r'^(?:access_token|id_token|refresh_token|code|credential|password|otp|auth|sessionid)$', re.I)


CONTROL_SCRIPT = r"""({stateKey, operation, revision, selector, text}) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).display !== 'none' &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).opacity !== '0';
  let state = window[stateKey];
  if (!state) {
    state = {document:crypto.randomUUID(),version:0,signature:'',nodes:new WeakMap()};
    Object.defineProperty(window,stateKey,{value:state});
  }
  const handle = e => {
    if (!state.nodes.has(e)) state.nodes.set(e,'control:'+crypto.randomUUID().replaceAll('-',''));
    return state.nodes.get(e);
  };
  const candidates = [...document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link]')];
  const controls = candidates.filter(e => visible(e) && !e.matches(':disabled') && !e.readOnly &&
    e.getAttribute('aria-disabled') !== 'true' &&
    !e.matches('input[type=hidden],input[type=password],input[type=file],input[autocomplete=one-time-code]'));
  const signature = JSON.stringify([location.href,
    candidates.map(e=>[handle(e),e.tagName,e.type,e.name,e.id,e.getAttribute('href'),e.value,e.checked,
      [...e.attributes].map(a=>[a.name,a.value]),
      visible(e),e.matches(':disabled'),e.readOnly,e.getAttribute('aria-disabled'),e.textContent,e.getAttribute('aria-label'),
      e.form ? handle(e.form) : null,e.getAttribute('formaction'),e.getAttribute('formmethod'),e.getAttribute('formtarget')]),
    [...document.querySelectorAll('form')].map(e=>[handle(e),e.action,e.method,e.target])]);
  if (signature !== state.signature) { state.signature=signature; state.version++; }
  const current = state.document+':'+state.version;
  const issued = controls.slice(0,40);
  if (operation === 'snapshot') return {revision:current, controls:issued.map(e=>({selector:handle(e),
    tag:['A','BUTTON','INPUT','TEXTAREA','SELECT'].includes(e.tagName) ? e.tagName.toLowerCase() : e.getAttribute('role') === 'link' ? 'a' : 'button',
    text:e.innerText||e.getAttribute('aria-label')||'',
    type:['INPUT','BUTTON'].includes(e.tagName) ? e.type : null}))};
  if (!['click','type'].includes(operation) || revision !== current) return {accepted:false};
  const target = issued.find(e=>handle(e)===selector);
  if (!target) return {accepted:false};
  if (operation === 'type' && !(target.tagName === 'TEXTAREA' || (target.tagName === 'INPUT' &&
      ['text','email','search','tel','url','number'].includes(target.type)))) return {accepted:false};
  if (operation === 'type' && target.maxLength >= 0 && text.length > target.maxLength) return {accepted:false};
  // This synchronous check and dispatch cannot switch to a replacement node.
  // Consume the revision before any event handler or navigation can run.
  state.signature=''; state.version++;
  if (operation === 'click') target.click();
  else {
    const proto = target.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,'value').set.call(target,text);
    target.dispatchEvent(new Event('input',{bubbles:true}));
    target.dispatchEvent(new Event('change',{bubbles:true}));
  }
  return {accepted:true};
}"""


def allowed_url(provider, url, *, resource=False):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.hostname or len(url) > 4096):
            return False
        host = parsed.hostname
        if resource:
            return any(host == base or host.endswith('.' + base) for base in RESOURCES[provider])
        path = unquote(parsed.path, errors='strict')
        if '%' in path or '\\' in path or any(p in {'.', '..'} for p in path.split('/')):
            return False
        return (host in APPLICATIONS[provider] and not PRIVATE_PATH.search(path)
                and not parsed.fragment and not any(PRIVATE_QUERY.fullmatch(key) for key, _ in parse_qsl(parsed.query)))
    except (ValueError, KeyError, TypeError):
        return False


class ConnectedBrowser:
    """No profile/cookies/CDP/JavaScript/file access is exposed by this adapter."""

    def __init__(self, website):
        self.website = website
        self.provider = website.provider
        self.context = website.context
        self.page = None
        self.epoch = 0
        self.revision = None
        self.controls = {}
        self._control_key = '_oak_content_' + secrets.token_hex(16)

    def allowed(self, url, *, resource=False):
        if self.provider != 'website':
            return allowed_url(self.provider, url, resource=resource)
        try:
            from .login_broker import website_target
            parsed = urlsplit(url)
            path = unquote(parsed.path, errors='strict')
            return (website_target(url) == self.website.destination and '%' not in path and '\\' not in path
                    and not any(p in {'.', '..'} for p in path.split('/'))
                    and (resource or not PRIVATE_PATH.search(path)))
        except (ValueError, TypeError):
            return False

    async def _route(self, route):
        request = route.request
        try:
            # Navigation to login, account security, or an external site never
            # yields pixels/DOM to the model. Resources use a fixed provider list.
            permitted = self.allowed(request.url, resource=not request.is_navigation_request())
            if permitted:
                if self.provider == 'website':
                    await route.fallback()
                else:
                    await route.continue_()
            else:
                await route.abort()
        except Exception:
            await route.abort()

    async def start(self):
        if self.provider == 'website':
            # The generic adapter has pinned this origin's public DNS. Keep its
            # route guard and current task document; never transfer a profile.
            self.page = self.website.page
            await self.context.route('**/*', self._route)
            self.page.on('framenavigated', lambda frame: self._navigate(frame))
            await self.checked()
            return
        await self.context.route('**/*', self._route)
        await self.context.unroute('**/*', self.website._route)
        listener = getattr(self.website, '_page_listener', None)
        if listener is not None:
            self.context.remove_listener('page', listener)
        # Destroy every credential-bearing document before any model operation.
        for page in tuple(self.context.pages):
            await page.close()
        self.page = await self.context.new_page()
        self.page.on('framenavigated', lambda frame: self._navigate(frame))
        self.context.on('page', lambda page: __import__('asyncio').create_task(page.close())
                        if page is not self.page else None)
        target = 'https://www.google.com/' if self.provider == 'google' else 'https://www.instagram.com/'
        await self.page.goto(target, wait_until='domcontentloaded', timeout=20000)
        await self.checked()

    def _navigate(self, frame):
        if self.page is not None and frame == self.page.main_frame:
            self.epoch += 1
            self.revision = None
            self.controls = {}

    async def checked(self):
        if (self.page is None or self.page.is_closed() or not self.allowed(self.page.url)
                or await self.page.locator('input[type="password"],input[autocomplete="one-time-code"]').count()):
            raise ValueError('Website login or an unsupported destination requires the human channel.')
        credentials = getattr(self.website, '_credential_values', ())
        if credentials:
            visible = self.page.url + '\n' + await self.page.title() + '\n' + await self.page.locator('body').inner_text()
            controls = await self.page.locator('input,textarea,select').evaluate_all(
                'els => els.filter(e=>e.getClientRects().length).map(e=>e.value||"").slice(0,200)')
            visible += '\n' + '\n'.join(controls)
            decoded = unquote(visible)
            normalized = re.sub(r'\s+', '', decoded)
            if any(value and (value in decoded or re.sub(r'\s+', '', value) in normalized) for value in credentials):
                raise ValueError('The website echoed private input; content remains in the human channel.')

    async def run(self, value):
        if not isinstance(value, dict) or value.get('action') not in {'open', 'read', 'click', 'type', 'screenshot'}:
            raise ValueError('Unsupported connected-browser action.')
        action = value['action']
        fields = {'action'} | ({'url'} if action == 'open' else {'selector', 'revision', 'text'} if action == 'type'
                              else {'selector', 'revision'} if action == 'click' else set())
        if set(value) != fields:
            raise ValueError('Invalid connected-browser fields.')
        if action == 'open' and not self.allowed(value['url']):
            raise ValueError('This URL is outside the selected provider applications.')
        if action in {'click', 'type'} and (not isinstance(value['selector'], str)
                or not re.fullmatch(r'control:[a-f0-9]{32}', value['selector'])
                or value['selector'] not in self.controls or value['revision'] != self.revision):
            raise ValueError('Use a current website control and revision returned by read.')
        if action == 'type' and (not isinstance(value['text'], str) or len(value['text']) > 16000):
            raise ValueError('Typed content is too large.')
        if action == 'screenshot' and getattr(self.website, '_credential_values', ()):
            # Pixels cannot be checked for an echoed secret atomically with an
            # arbitrary site's JavaScript. Text/control output is checked below.
            raise ValueError('Після введення пароля або коду використовуй read; знімок залишається недоступним.')
        try:
            await self.checked()
            if action == 'open':
                await self.page.goto(value['url'], wait_until='domcontentloaded', timeout=20000)
            elif action in {'click', 'type'}:
                applied = await self.page.evaluate(CONTROL_SCRIPT, {'stateKey': self._control_key,
                    'operation': action, 'selector': value['selector'], 'revision': value['revision'],
                    'text': value.get('text')})
                self.revision, self.controls = None, {}
                if applied != {'accepted': True}:
                    raise ValueError('Website controls changed; read again before acting.')
            await self.checked()
            epoch = self.epoch
            snapshot = await self.page.evaluate(CONTROL_SCRIPT, {'stateKey': self._control_key, 'operation': 'snapshot'})
            if (not isinstance(snapshot, dict) or set(snapshot) != {'revision', 'controls'}
                    or not re.fullmatch(r'[a-f0-9-]{36}:\d{1,10}', str(snapshot['revision']))
                    or not isinstance(snapshot['controls'], list) or len(snapshot['controls']) > 40
                    or any(not isinstance(item, dict) or set(item) != {'selector', 'tag', 'text', 'type'}
                           or not re.fullmatch(r'control:[a-f0-9]{32}', str(item['selector']))
                           or item['tag'] not in {'a', 'button', 'input', 'textarea', 'select'}
                           or not isinstance(item['text'], str) or len(item['text']) > 65536
                           or (item['type'] is not None and not isinstance(item['type'], str))
                           for item in snapshot['controls'])):
                raise ValueError('Website control snapshot unavailable.')
            result = {'provider': self.provider, 'url': self.page.url, 'title': await self.page.title()}
            result.update(revision=snapshot['revision'], controls=snapshot['controls'])
            if action == 'screenshot':
                data = await self.page.screenshot(type='png')
                if len(data) > 1024 * 1024:
                    raise ValueError('Screenshot is too large.')
                result['image_base64'] = base64.b64encode(data).decode('ascii')
            else:
                result['text'] = await self.page.locator('body').inner_text()
            await self.checked()
            if self.epoch != epoch:
                raise ValueError('Website navigation changed during capture.')
            # A provider may put private input into control labels or URLs too.
            def strings(item):
                if isinstance(item, str):
                    yield item
                elif isinstance(item, dict):
                    for key, value in item.items():
                        if key != 'image_base64':
                            yield from strings(value)
                elif isinstance(item, list):
                    for value in item:
                        yield from strings(value)
            public = '\n'.join(unquote(value) for value in strings(result))
            normalized = re.sub(r'\s+', '', public)
            if any(secret and (secret in public or re.sub(r'\s+', '', secret) in normalized)
                   for secret in getattr(self.website, '_credential_values', ())):
                raise ValueError('The website echoed private input.')
            # Scan complete strings before truncation; otherwise a long secret
            # could leak its prefix while no longer matching the full value.
            result['title'] = result['title'][:300]
            if 'text' in result:
                result['text'] = result['text'][:16000]
            for item in result['controls']:
                item['text'] = item['text'][:120]
            self.revision = snapshot['revision']
            self.controls = {item['selector']: dict(item) for item in snapshot['controls']}
            return result
        except Exception:
            # Browser exceptions may embed typed content and credential URLs.
            raise ValueError('Дія в авторизованому браузері не виконана; може бути потрібний новий вхід.') from None
