"""Bounded, human-driven website login adapters; no OAuth or cookie export.

Provider layouts and challenges can change. Unknown UI is unavailable, never an
authentication success. Credentials exist only in the human request and browser;
neither snapshots nor exception messages contain provider text or entered values.
The broker must isolate this process and retain ownership of the browser lease.
"""

import asyncio
import re
import secrets
from urllib.parse import urlsplit


PROVIDERS = {
    'google': {
        'url': 'https://accounts.google.com/ServiceLogin?hl=en&continue=https%3A%2F%2Fmyaccount.google.com%2F',
        'origins': {'https://accounts.google.com', 'https://myaccount.google.com'},
        'resources': {'accounts.google.com', 'myaccount.google.com', 'www.google.com',
                      'www.gstatic.com', 'ssl.gstatic.com', 'accounts.gstatic.com',
                      'fonts.gstatic.com', 'lh3.googleusercontent.com'},
    },
    'instagram': {
        'url': 'https://www.instagram.com/accounts/login/?hl=en',
        'origins': {'https://www.instagram.com'},
        'resources': {'www.instagram.com', 'static.cdninstagram.com'},
    },
}

FIELDS = {
    'identifier': {'name': 'identifier', 'type': 'text', 'label': 'Пошта, телефон або ім’я користувача', 'max_length': 254},
    'password': {'name': 'password', 'type': 'password', 'label': 'Пароль', 'max_length': 1024},
    'otp': {'name': 'otp', 'type': 'text', 'label': 'Код підтвердження', 'max_length': 16},
}
STAGE_FIELDS = {'identifier': ['identifier'], 'password': ['password'], 'otp': ['otp'],
                'login': ['identifier', 'password'], 'device_approval': [],
                'provider_blocked': [], 'unsupported_challenge': [], 'unsupported_layout': [], 'authenticated': []}
MESSAGES = {
    'identifier': 'Введи дані для входу. Вони підуть лише до сторінки провайдера.',
    'password': 'Введи пароль для поточного кроку входу.',
    'otp': 'Введи код із вибраного провайдером способу підтвердження.',
    'login': 'Введи дані для входу на сайт.',
    'device_approval': 'Підтвердь цей вхід на своєму пристрої. Очікуємо відповідь провайдера.',
    'provider_blocked': 'Провайдер заблокував цей спосіб входу. Обхід перевірки недоступний.',
    'unsupported_challenge': 'Цей крок входу не підтримується формою. Можна скасувати запит.',
    'unsupported_layout': 'Не вдалося розпізнати форму цієї сторінки. Можна скасувати запит.',
    'authenticated': 'Вхід підтверджено сторінкою облікового запису.',
}


class RecoverableFormError(ValueError):
    """Only a read or a revision check before any input needs refreshing."""
    def __init__(self, code):
        if code not in {'stale_form', 'navigation_pending', 'composition_unavailable'}:
            raise ValueError('Invalid form diagnostic.')
        self.code = code
        super().__init__(code)


class SubmitUncertainError(ValueError):
    """An input operation was dispatched; it must never be replayed."""
    code = 'submit_uncertain'

    def __init__(self):
        super().__init__(self.code)


# This function never returns textContent, input values, URLs with query strings,
# cookies, or raw HTML. Form values used for fingerprinting remain in the page.
# Each fill and the final click recheck the origin and form fingerprint. The
# model and human client cannot supply selectors or JavaScript.
FORM_SCRIPT = r"""async ({provider, stateKey, operation, revision, values}) => {
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const active = e => visible(e) && !e.disabled && !e.readOnly;
  const one = selector => {
    const list = [...document.querySelectorAll(selector)].filter(active);
    return list.length === 1 ? list[0] : null;
  };
  const nextButton = selector => {
    const list = [...document.querySelectorAll(selector)].filter(visible);
    return list.length === 1 ? list[0] : null;
  };
  const origin = location.origin, path = location.pathname;
  const allowed = provider === 'google' ?
    ['https://accounts.google.com', 'https://myaccount.google.com'] : ['https://www.instagram.com'];
  let state = window[stateKey];
  if (!state) {
    state = {document: crypto.randomUUID(), revision: 0, signature: '', nodes: new WeakMap(), next: 0};
    Object.defineProperty(window, stateKey, {value: state});
  }
  const id = e => { if (!state.nodes.has(e)) state.nodes.set(e, ++state.next); return state.nodes.get(e); };
  const safeForm = e => {
    try { return !!e.form && e.form.method.toLowerCase() === 'post' &&
      new URL(e.form.action, location.href).origin === location.origin && !e.form.target; }
    catch { return false; }
  };
  const safeLink = (e, expectedOrigin, expectedPath) => {
    try { const u = new URL(e.href, location.href); return u.origin === expectedOrigin && expectedPath.test(u.pathname); }
    catch { return false; }
  };
  const buttonForm = e => e?.form || e?.closest('form');
  function inspect() {
    const body = (document.body?.innerText || '').toLowerCase();
    const inputs = [...document.querySelectorAll('input')].filter(e => active(e) &&
      !['hidden', 'submit', 'button', 'checkbox', 'radio'].includes(e.type));
    const credentials = inputs.some(e => e.type === 'password' || [e.id, e.name].some(name =>
      /^(identifierId|username|Passwd|totpPin|idvPin|verificationCode|security_code)$/.test(name)));
    const blocked = /this browser or app may not be secure|couldn.t sign you in|automated queries|unusual traffic|temporarily blocked|try again later/.test(body);
    const captcha = [...document.querySelectorAll('iframe[src*="captcha"],input[name*="captcha"]')].some(visible);
    const device = /check your (phone|other device)|approve (the|this) (sign.?in|login)|sent a notification|check your notifications/.test(body);
    const invalid = /wrong password|incorrect password|incorrect code|invalid code|password was incorrect|code was incorrect/.test(body);
    const result = {stage: 'unsupported_layout', fields: [], controls: {}, button: null, dismiss: false, cookies: false, invalid};
    if (!allowed.includes(location.origin) || blocked) result.stage = 'provider_blocked';
    else if (captcha) result.stage = 'unsupported_challenge';
    else if (provider === 'google') {
      const menu = [...document.querySelectorAll('a[aria-label]')].some(e => visible(e) &&
        /google account:/i.test(e.getAttribute('aria-label') || '') &&
        /[^\s@]+@[^\s@]+\.[^\s@]+/.test(e.getAttribute('aria-label') || '') &&
        safeLink(e, 'https://accounts.google.com', /^\/SignOutOptions$/));
      const management = [...document.querySelectorAll('a[href]')].some(e => visible(e) &&
        safeLink(e, 'https://myaccount.google.com', /^\/(personal-info|security)(\/|$)/));
      if (location.origin === 'https://myaccount.google.com' && menu && management && !credentials)
        result.stage = 'authenticated';
      else if (device && location.origin === 'https://accounts.google.com') result.stage = 'device_approval';
      else if (location.origin === 'https://accounts.google.com' && /\/signin\//.test(path)) {
        const choices = [
          ['identifier', 'input#identifierId[type="email"],input#identifierId[type="text"],input#identifierId[type="tel"]', 'button#identifierNext, #identifierNext button'],
          ['password', 'input[name="Passwd"][type="password"]', 'button#passwordNext, #passwordNext button'],
          ['otp', 'input#totpPin', 'button#totpNext, #totpNext button'],
          ['otp', 'input#idvPin', 'button#idvPreregisteredPhoneNext, #idvPreregisteredPhoneNext button, button#idvAnyPhonePinNext, #idvAnyPhonePinNext button'],
          ['otp', 'input#backupCodePin', 'button#backupCodeNext, #backupCodeNext button'],
        ];
        for (const [stage, selector, next] of choices) {
          const field = one(selector), button = nextButton(next);
          if (field && button && inputs.length === 1 && safeForm(field) && button.form === field.form) {
            result.stage = stage; result.fields = [stage]; result.controls[stage] = field; result.button = button; break;
          }
        }
      }
    } else {
      const inbox = [...document.querySelectorAll('a[href]')].some(e => visible(e) &&
        safeLink(e, origin, /^\/direct\/inbox\/?$/));
      const compose = [...document.querySelectorAll('svg[aria-label="New post"]')].some(visible);
      const profile = [...document.querySelectorAll('a[href]')].some(e => visible(e) &&
        safeLink(e, origin, /^\/[A-Za-z0-9._]+\/$/) &&
        [...e.querySelectorAll('img[alt]')].some(img => /profile picture$/i.test(img.alt || '')));
      if (inbox && compose && profile && !/\/(accounts|challenge)\//.test(path) && !credentials)
        result.stage = 'authenticated';
      else if (device) result.stage = 'device_approval';
      else if (/^\/accounts\/login\//.test(path) || /^\/challenge\//.test(path)) {
        const identifier = one('input[name="username"],form#login_form input[name="email"]');
        const password = one('input[name="password"][type="password"],form#login_form input[name="pass"][type="password"]');
        const otp = one('input[name="verificationCode"],input[name="security_code"]');
        const roleButtons = [...document.querySelectorAll('form#login_form [role="button"]')]
          .filter(e => visible(e) && /^log in$/i.test(e.textContent.trim()));
        const button = roleButtons.length === 1 ? roleButtons[0] : nextButton('button[type="submit"]');
        const fields = otp ? ['otp'] : identifier && password ? ['identifier', 'password'] : [];
        const controls = {identifier, password, otp};
        if (fields.length && inputs.length === fields.length && button && fields.every(name =>
            safeForm(controls[name]) && controls[name].form === buttonForm(button))) {
          result.stage = otp ? 'otp' : 'login'; result.fields = fields; result.controls = controls; result.button = button;
        }
      }
      if (result.stage === 'unsupported_layout' && inputs.length === 0 &&
          /save your login info|save your login information|turn on notifications/.test(body)) {
        const buttons = [...document.querySelectorAll('button')].filter(e => active(e) && /^not now$/i.test(e.textContent.trim()));
        if (buttons.length === 1) { result.stage = 'login'; result.button = buttons[0]; result.dismiss = true; }
      }
      const decline = [...document.querySelectorAll('button')].filter(e => active(e) && /^decline optional cookies$/i.test(e.textContent.trim()));
      if (decline.length === 1) {
        result.stage = 'login'; result.fields = []; result.button = decline[0]; result.dismiss = true; result.cookies = true;
      }
    }
    const signature = JSON.stringify([location.href, result.stage, result.dismiss, result.cookies,
      [...document.querySelectorAll('input,button,form,[role="button"]')].map(e => [id(e), e.tagName, e.id, e.name, e.type,
        e.autocomplete, e.tagName === 'BUTTON' ? null : !!e.disabled, !!e.readOnly, visible(e), e.form ? id(e.form) : null,
        e.tagName === 'FORM' ? [e.action, e.method, e.target] : null,
        e.type === 'hidden' ? e.value : null]), result.fields,
      result.button ? [id(result.button), result.button.textContent, buttonForm(result.button) ? id(buttonForm(result.button)) : null] : null]);
    if (signature !== state.signature) { state.signature = signature; state.revision++; }
    result.revision = state.document + ':' + state.revision;
    return result;
  }
  const initial = inspect();
  const publicResult = value => ({stage: value.stage, revision: value.revision, fields: value.fields,
    can_submit: !!value.button, dismiss: value.dismiss, cookies: value.cookies, invalid: value.invalid, origin: location.origin});
  if (operation === 'snapshot') return publicResult(initial);
  if (operation !== 'submit' || initial.revision !== revision || !initial.button ||
      Object.keys(values).sort().join(',') !== [...initial.fields].sort().join(','))
    return {accepted: false, code: 'stale_form'};
  let touched = false;
  const rejected = () => ({accepted: false, code: touched ? 'submit_uncertain' : 'stale_form'});
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  for (const name of initial.fields) {
    const current = inspect();
    if (current.revision !== revision || location.origin !== origin || !safeForm(current.controls[name]))
      return rejected();
    touched = true;
    setter.call(current.controls[name], values[name]);
    current.controls[name].dispatchEvent(new Event('input', {bubbles: true}));
    current.controls[name].dispatchEvent(new Event('change', {bubbles: true}));
    await new Promise(resolve => requestAnimationFrame(resolve));
  }
  const current = inspect();
  if (current.revision !== revision || location.origin !== origin || !current.button || current.button.disabled || current.button.getAttribute('aria-disabled') === 'true' ||
      (!current.dismiss && !current.fields.every(name => safeForm(current.controls[name]) && current.controls[name].form === buttonForm(current.button))))
    return rejected();
  // Consume this revision before click; an uncertain outcome is never replayed.
  state.signature = ''; state.revision++;
  current.button.click();
  return {accepted: true};
}"""


class WebsiteBrowser:
    OBSERVATION_SECONDS = 2.0

    def __init__(self, provider, *, playwright_factory=None):
        if provider not in PROVIDERS:
            raise ValueError('Unsupported website provider.')
        self.provider, self.config = provider, PROVIDERS[provider]
        self._factory = playwright_factory
        self._state_key = '_oak_login_' + secrets.token_hex(16)
        self._blocked = False
        self._lock = asyncio.Lock()
        self.manager = self.driver = self.browser = self.context = self.page = None
        self.startup = self.launch = self.browser_close = self.shutdown = None
        self._page_listener = None
        self._credential_values = []

    async def _route(self, route):
        try:
            request = route.request
            parsed = urlsplit(request.url)
            origin = parsed.scheme + '://' + parsed.netloc
            main = request.is_navigation_request() and request.frame == self.page.main_frame
            secure = parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in (None, 443)
            allowed = secure and (origin in self.config['origins'] if main else
                                  parsed.hostname in self.config['resources'] or
                                  (self.provider == 'instagram' and parsed.hostname.endswith('.cdninstagram.com')))
            if request.is_navigation_request() and not main:
                allowed = False
            if not allowed:
                if main:
                    self._blocked = True
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            self._blocked = True
            try:
                await route.abort()
            except Exception:
                pass

    async def _close_popup(self, page):
        if page != self.page:
            try:
                await page.close()
            except Exception:
                self._blocked = True

    def _launch_options(self):
        return {'headless': True, 'chromium_sandbox': True,
                'args': ['--disable-breakpad', '--disable-crash-reporter', '--disable-sync', '--disable-save-password-bubble']}

    async def _configure_context(self):
        pass

    async def _configure_page(self):
        """Install trusted page guards before the first document request."""
        pass

    async def _initial_navigation(self):
        await self.page.goto(self.config['url'], wait_until='domcontentloaded', timeout=30000)

    async def start(self):
        if self.manager is not None:
            raise ValueError('Website browser already started.')
        if self._factory is None:
            from playwright.async_api import async_playwright
            self._factory = async_playwright
        try:
            self.manager = self._factory()
            self.startup = asyncio.create_task(self.manager.start())
            self.driver = await asyncio.shield(self.startup)
            self.launch = asyncio.create_task(self.driver.chromium.launch(**self._launch_options()))
            self.browser = await asyncio.shield(self.launch)
            self.context = await self.browser.new_context(locale='en-US', accept_downloads=False,
                                                          service_workers='block')
            await self._configure_context()
            self.page = await self.context.new_page()
            self._page_listener = lambda page: asyncio.create_task(self._close_popup(page))
            self.context.on('page', self._page_listener)
            await self.context.route('**/*', self._route)
            await self._configure_page()
            await self._initial_navigation()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError('Website login could not start.') from None

    async def _inspect(self):
        if self.page is None or self._blocked:
            return {'stage': 'provider_blocked', 'revision': 'unavailable', 'fields': [], 'can_submit': False}
        parsed = urlsplit(self.page.url)
        if parsed.scheme + '://' + parsed.netloc not in self.config['origins']:
            self._blocked = True
            return {'stage': 'provider_blocked', 'revision': 'unavailable', 'fields': [], 'can_submit': False}
        result = await self.page.evaluate(FORM_SCRIPT, {'provider': self.provider,
            'stateKey': self._state_key, 'operation': 'snapshot'})
        if (not isinstance(result, dict) or result.get('stage') not in STAGE_FIELDS
                or result.get('origin') not in self.config['origins']
                or not re.fullmatch(r'[a-f0-9-]{36}:\d{1,10}', str(result.get('revision', '')))
                or type(result.get('can_submit')) is not bool
                or (result.get('dismiss') and (self.provider != 'instagram' or result['stage'] != 'login'))
                or result.get('fields') != ([] if result.get('dismiss') else STAGE_FIELDS[result['stage']])):
            raise ValueError('Website form unavailable.')
        return result

    async def _observe(self):
        """Retry only reads while a document is changing; never retry input."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.OBSERVATION_SECONDS
        while True:
            loading = False
            try:
                # An old document can report loaded just before it is replaced.
                # The inspection below must therefore also handle context loss.
                wait = getattr(self.page, 'wait_for_load_state', None)
                if wait is not None:
                    loading = True
                    remaining = max(0.001, deadline - loop.time())
                    await asyncio.wait_for(wait('domcontentloaded', timeout=remaining * 1000), remaining)
                    loading = False
                return await asyncio.wait_for(self._inspect(), max(0.001, deadline - loop.time()))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Browser diagnostics stay private; only a fixed enum crosses IPC.
                navigation = any(message in str(error) for message in (
                    'Execution context was destroyed', 'Cannot find context with specified id'))
                timeout = isinstance(error, TimeoutError) or (loading and type(error).__name__ == 'TimeoutError')
                if not navigation and not timeout:
                    raise
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise RecoverableFormError('navigation_pending') from None
                await asyncio.sleep(min(0.05, remaining))

    async def snapshot(self):
        async with self._lock:
            try:
                state = await self._observe()
                message = MESSAGES[state['stage']]
                if state.get('cookies'):
                    message = 'Продовжимо лише з необхідними кукі. Необов’язкові кукі буде відхилено.'
                elif state.get('dismiss'):
                    message = 'Продовжимо без збереження даних входу або ввімкнення сповіщень.'
                elif state.get('invalid') and state['fields']:
                    message = 'Провайдер не прийняв введені дані. Перевір їх і спробуй ще раз.'
                return {'provider': self.provider, 'stage': state['stage'], 'revision': state['revision'],
                        'fields': [dict(FIELDS[name]) for name in state['fields']],
                        'actions': [{'id': 'submit', 'label': 'Продовжити'}] if state['can_submit'] else [],
                        'message': message}
            except asyncio.CancelledError:
                raise
            except RecoverableFormError:
                raise
            except Exception:
                raise ValueError('Website form unavailable.') from None

    async def submit(self, revision, action, values):
        async with self._lock:
            try:
                state = await self._observe()
                if revision != state['revision']:
                    raise RecoverableFormError('stale_form')
                if (action != 'submit' or not state['can_submit']
                        or not isinstance(values, dict) or set(values) != set(state['fields'])):
                    raise ValueError
                for name, value in values.items():
                    if not isinstance(value, str) or not 0 < len(value) <= FIELDS[name]['max_length'] or '\x00' in value:
                        raise ValueError
                    if name == 'otp' and not re.fullmatch(r'[0-9 -]{4,16}', value):
                        raise ValueError
                credentials = list(dict.fromkeys(self._credential_values + [value for name, value in values.items()
                    if name in {'password', 'otp'} and value]))
                if len(credentials) > 128:
                    raise ValueError
                self._credential_values = credentials
            except asyncio.CancelledError:
                raise
            except RecoverableFormError:
                raise
            except Exception:
                raise ValueError('Website input rejected.') from None
            try:
                result = await self.page.evaluate(FORM_SCRIPT, {'provider': self.provider,
                    'stateKey': self._state_key, 'operation': 'submit', 'revision': revision, 'values': values})
            except asyncio.CancelledError:
                raise
            except Exception:
                raise SubmitUncertainError() from None
            if result == {'accepted': False, 'code': 'stale_form'}:
                raise RecoverableFormError('stale_form')
            if result != {'accepted': True}:
                raise SubmitUncertainError()

    async def verified(self):
        return (await self.snapshot())['stage'] == 'authenticated'

    async def close(self):
        try:
            await self._close_resources()
        finally:
            self._credential_values.clear()

    async def _close_resources(self):
        def failed(task):
            return task is not None and task.done() and (task.cancelled() or task.exception() is not None)

        async def settle(task):
            if task is None:
                return None
            try:
                return await asyncio.wait_for(asyncio.shield(task), 5)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
            except Exception:
                return None
        if self.driver is None:
            self.driver = await settle(self.startup)
        if self.browser is None:
            self.browser = await settle(self.launch)
        if self.browser:
            if self.browser_close is None or failed(self.browser_close):
                self.browser_close = asyncio.create_task(self.browser.close())
            await asyncio.wait_for(asyncio.shield(self.browser_close), 10)
            self.browser = self.context = self.page = None
            self.launch = self.browser_close = None
        if self.driver or self.manager:
            if self.shutdown is None or failed(self.shutdown):
                self.shutdown = asyncio.create_task(self.driver.stop() if self.driver else self.manager.__aexit__(None, None, None))
            await asyncio.wait_for(asyncio.shield(self.shutdown), 10)
        self.driver = self.manager = self.startup = self.launch = self.browser_close = self.shutdown = self._page_listener = None
