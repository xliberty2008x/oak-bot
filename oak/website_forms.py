"""Human-operated forms at one explicitly approved public HTTPS origin.

No site template is required. DOM labels are bounded plain text and field/button
identifiers are opaque. Unknown sites never produce an authenticated verdict;
the broker can record a separate explicit owner confirmation after rechecking
the current revision. This is not a CAPTCHA or browser-security bypass.
"""

import asyncio
from copy import deepcopy
import ipaddress
import re
import socket
import uuid
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .signin_providers import FORM_SCRIPT, PROVIDERS, RecoverableFormError, SubmitUncertainError, WebsiteBrowser
from .website_ui import compile_plan, model_context
from .website_resources import StaticWebsiteResources, validate_static_resources
from .website_scene import MAX_CONTROLS, MAX_SELECT_OPTIONS, compile_plan as compile_scene, validate_scene, _widget_label
from .website_scene_planner import _eligible_anchors, is_anchor_abstention
from .website_planner import WebsitePlannerError
from .website_scene_dom import SCENE_SCRIPT
from .website_scene_sanitize import sanitize_scene
from .website_auth_hosts import WebsiteAuthHosts


SENSITIVE_QUERY = re.compile(r'password|passwd|secret|token|credential|session|^code$|^otp$|^auth$|^api_key$|^key$', re.I)
FIELD_TYPES = {'text': 1024, 'email': 254, 'password': 1024, 'otp': 16, 'checkbox': 1, 'select': 64}
BLOCK_MESSAGES = {
    'origin_policy': 'Oak зупинив перехід за межі схваленої адреси. Цей запит не дозволяє продовження на іншому сайті. Можна скасувати запит.',
    'popup': 'Сайт відкрив додаткове вікно. Oak закрив його: цей спосіб входу поки не підтримується. Можна скасувати запит.',
    'http_error': 'Сайт повернув помилку завантаження сторінки. Причину відмови не підтверджено. Можна скасувати запит.',
    'page_rejection': 'Сторінка повідомляє про блокування цього способу входу. Можна скасувати запит.',
    'network_error': 'Не вдалося завантажити сторінку через помилку з’єднання. Відмову сайту не підтверджено. Можна скасувати запит.',
    'route_error': 'Oak не зміг безпечно обробити запит сторінки й зупинив продовження. Можна скасувати запит.',
    'unknown': 'Не вдалося продовжити цей крок. Причину блокування не підтверджено. Можна скасувати запит.',
}
MESSAGES = {
    'clarification': 'Обери, для якої дії показати поля. Вибір не виконує дію на сайті.',
    'form': 'Заповни поля поточної сторінки. Дані передаються лише схваленому сайту.',
    'review': 'Форма більше не потребує введення. Підтвердь завершення лише якщо ти впевнений: це твоє підтвердження, а не перевірка входу сайтом.',
    'authenticated': 'Вхід підтверджено сторінкою провайдера.',
    'provider_blocked': BLOCK_MESSAGES['unknown'],
    'unsupported_challenge': 'Сайт просить перевірку, яку ця форма не підтримує. Її не буде пройдено автоматично.',
    'unsupported_layout': 'Не вдалося розпізнати форму цієї сторінки. Можна оновити сторінку або скасувати запит.',
    'device_approval': 'Підтвердь дію на своєму пристрої. Очікуємо відповідь сайту.',
}


def validated_url(value):
    """Validate syntax only; start() additionally resolves and pins public DNS."""
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or not parsed.hostname or '%' in parsed.netloc
                or any(SENSITIVE_QUERY.search(k) for k, _ in parse_qsl(parsed.query))):
            raise ValueError
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if host.endswith('.') or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '.' not in host:
                raise ValueError from None
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
                raise ValueError
        else:
            if not public_address(address):
                raise ValueError
        authority = '[' + host + ']' if ':' in host else host
        return urlunsplit(('https', authority, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError, TypeError):
        raise ValueError('A public HTTPS website address without credentials is required.') from None


def public_address(value):
    try:
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and not address.is_reserved
                and not address.is_loopback and not address.is_link_local and not address.is_unspecified)
    except ValueError:
        return False


GENERIC_SCRIPT = r"""async ({stateKey, expectedOrigin, operation, revision, action, values, mayConfirm, redactions, credentialValues, groupMode, groupId, publicContext}) => {
  const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const active = e => visible(e) && !e.disabled && !e.readOnly;
  let state = window[stateKey];
  if (!state) {
    state = {document: crypto.randomUUID(), revision: 0, signature: '', nodes: new WeakMap(), next: 0, secrets: []};
    Object.defineProperty(window, stateKey, {value: state});
  }
  state.secrets = [...new Set([...state.secrets, ...(redactions || [])])].slice(-128);
  const id = e => { if (!state.nodes.has(e)) state.nodes.set(e, ++state.next); return state.nodes.get(e); };
  const clean = (text, fallback) => {
    let label = String(text || '').replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim();
    const entered = [...state.secrets, ...[...document.querySelectorAll('input,textarea,select')]
      .filter(e => !['submit', 'button', 'reset'].includes(e.type) &&
        (!['checkbox','radio'].includes(e.type) || e.hasAttribute('value'))).map(e => e.value)].filter(Boolean);
    for (const value of entered.sort((a,b)=>b.length-a.length)) label = label.split(value).join('•••');
    label = label.replace(/[^\s@]+@[^\s@]+\.[^\s@]+/g, '•••')
      .replace(/(?:https?:\/\/|www\.)[^\s<>]+/gi, '•••')
      .replace(/\b[A-Za-z0-9_-]{24,}\b/g, '•••');
    return label.slice(0, 120) || fallback;
  };
  const caption = e => {
    // A wrapping label may contain a select/textarea. Its caption must not
    // incorporate option text or default field contents, including descendants.
    const walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT, {acceptNode:n =>
      n.parentElement && visible(n.parentElement) && !n.parentElement.closest('input,textarea,select,button') ?
        NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT});
    let text='', node;
    while ((node=walker.nextNode())) text += ' '+node.textContent;
    return text;
  };
  const captionFor = e => e.getAttribute('aria-label') || [...(e.labels || [])].filter(visible).map(caption).join(' ') || e.getAttribute('placeholder');
  const labelFor = e => clean(captionFor(e), 'Поле');
  const autocompleteRole = e => {
    // The browser's IDL property already parses the HTML autofill grammar.
    // Section names and other arbitrary attribute text remain private.
    const tokens = String(e.autocomplete || '').split(' ');
    if (tokens.at(-1) === 'webauthn') tokens.pop();
    const role = tokens.at(-1);
    return ['username','current-password','new-password','one-time-code','email','tel'].includes(role) ? role : null;
  };
  const kind = e => {
    if (e.tagName === 'SELECT') return e.multiple ? null : 'select';
    if (e.tagName === 'TEXTAREA') return 'text';
    if (autocompleteRole(e) === 'one-time-code' || /(?:^|[_-])(otp|totp|verification_?code|security_?code)(?:$|[_-])/i.test(e.name + '_' + e.id)) return 'otp';
    return ({text:'text', email:'email', password:'password', tel:'text', number:'text', search:'text', checkbox:'checkbox'})[e.type] || null;
  };
  const formOf = e => e.form || e.closest('form');
  const scriptOnlyForm = form => !!form && !form.hasAttribute('method') && !form.hasAttribute('action') &&
    !form.hasAttribute('target') && form.method.toLowerCase() === 'get' &&
    [...form.elements].some(e => active(e) && kind(e));
  const safeForm = (form, button) => {
    try {
      if (form && (new URL(form.action, location.href).origin !== expectedOrigin || form.target ||
          (form.method.toLowerCase() !== 'post' && !scriptOnlyForm(form)))) return false;
      if (form?.method.toLowerCase() === 'post' && state.nativeFormsDisabled === true) return false;
      if (button?.hasAttribute('formaction') && new URL(button.formAction, location.href).origin !== expectedOrigin) return false;
      if (button?.getAttribute('formtarget') || (button?.getAttribute('formmethod') && button.formMethod.toLowerCase() !== 'post')) return false;
      if (button?.getAttribute('target') || button?.hasAttribute('download')) return false;
      if (button?.hasAttribute('href')) {
        const target = new URL(button.getAttribute('href'), location.href);
        if (target.origin !== expectedOrigin || target.username || target.password) return false;
      }
      return true;
    } catch { return false; }
  };
  function inspect() {
    const body = (document.body?.innerText || '').toLowerCase();
    const controls = [...document.querySelectorAll('input,textarea,select')].filter(e => active(e) && !['hidden','submit','button','reset'].includes(e.type));
    const challenge = [...document.querySelectorAll('iframe,input')].some(e => visible(e) && /captcha|recaptcha|hcaptcha|turnstile/i.test([e.name,e.id,e.src].join(' ')));
    const secrets = credentialValues || [];
    const matchesSecret = value => secrets.some(secret => secret && (value.includes(secret) ||
      value.replace(/\s+/g,' ').includes(secret.replace(/\s+/g,' '))));
    const credentials = [...document.querySelectorAll('input,textarea,select')].some(e => e.type === 'password' || kind(e) === 'otp' ||
      (visible(e) && matchesSecret(e.value || ''))) || matchesSecret(document.body?.innerText || '');
    let stage = 'unsupported_layout', fields = [], actions = [], selected = [], buttons = [], groups = [], tracked = [];
    if (location.origin !== expectedOrigin) stage = 'provider_blocked';
    else if (state.nativeFormBlocked === true) stage = 'unsupported_layout';
    else if (/this browser or app may not be secure|automated queries|unusual traffic|temporarily blocked/.test(body)) stage = 'provider_blocked';
    else if (challenge) stage = 'unsupported_challenge';
    else if (/check your (phone|other device)|approve (the|this) (sign.?in|login)|sent a notification/.test(body)) stage = 'device_approval';
    else {
      const visibleActions = [...document.querySelectorAll('button,input[type=submit],input[type=button],[role=button],a[href],[role=link]')]
        .filter(e => visible(e) && e.type !== 'reset');
      const formGroups = [...new Set([...controls, ...visibleActions].map(formOf))].filter(Boolean);
      const candidates = formGroups.map(form => ({form, controls:controls.filter(e => formOf(e) === form),
        buttons:visibleActions.filter(e => formOf(e) === form && safeForm(form, e))}));
      const looseActions = visibleActions.filter(e => !formOf(e) && safeForm(null, e));
      const link = e => e.tagName === 'A' || e.getAttribute('role') === 'link';
      // A standalone navigation choice has no association with unrelated page
      // inputs (for example a language selector). It never fills those inputs.
      candidates.push({form:null, navigation:true, controls:[], buttons:looseActions.filter(link)},
        {form:null, controls:controls.filter(e => !formOf(e)), buttons:looseActions.filter(e => !link(e))});
      const supported = candidates.filter(group => safeForm(group.form) && group.controls.length <= 16 && group.buttons.length > 0 && group.buttons.length <= 8 &&
          group.controls.every(e => kind(e) && (kind(e) !== 'select' || (e.options.length > 0 && e.options.length <= 32))));
      const describe = candidate => {
        const description = {id:'g'+id(candidate.form || (candidate.navigation ? document.documentElement : document.body)),
        fields:candidate.controls.map(e => {
          const type = kind(e), limit = ({text:1024,email:254,password:1024,otp:16,checkbox:1,select:64})[type];
          const result = {name:'f'+id(e), type, label:labelFor(e), max_length:e.maxLength > 0 ? Math.min(limit,e.maxLength) : limit};
          if (type === 'select') result.options = [...e.options].filter(option => !option.disabled).map(option => ({value:'o'+id(option),label:clean(option.label,'Варіант')}));
          return result;
        }),
        actions:candidate.buttons.map(e => ({id:'a'+id(e),label:clean(e.innerText || e.getAttribute('aria-label') || (e.type === 'submit' ? e.value : ''),'Продовжити')}))};
        if (groupMode === true) {
          const compatible = {'username':['text','email'], 'email':['text','email'], 'tel':['text'],
            'current-password':['password'], 'new-password':['password'], 'one-time-code':['otp']};
          const roles = Object.fromEntries(candidate.controls.map(e => [e, autocompleteRole(e)])
            .filter(([e, role]) => compatible[role]?.includes(kind(e))).map(([e, role]) => ['f'+id(e), role]));
          if (Object.keys(roles).length) description.field_roles = roles;
        }
        return description;
      };
      if (!controls.length && !visibleActions.length && !credentials && ![...document.querySelectorAll('form')].some(visible)) stage = mayConfirm === true ? 'review' : 'unsupported_layout';
      else if (groupMode === true ? supported.length > 0 && supported.length <= 8 : supported.length === 1) {
        tracked = supported;
        const descriptions = supported.map(describe);
        if (groupMode === true) groups = descriptions;
        const index = groupMode === true && groupId ? descriptions.findIndex(group => group.id === groupId) :
          supported.length === 1 ? 0 : -1;
        if (index >= 0) {
          selected = supported[index].controls; buttons = supported[index].buttons;
          fields = descriptions[index].fields; actions = descriptions[index].actions;
        }
        stage = 'form';
      }
    }
    if (stage !== 'form') { fields = []; actions = []; selected = []; buttons = []; }
    const confirmable = mayConfirm === true && !credentials && (stage === 'review' || stage === 'form');
    const allButtons = tracked.flatMap(group => group.buttons);
    const belongs = (e, group) => group.navigation ? group.buttons.includes(e) :
      e === group.form || formOf(e) === group.form || group.controls.some(control => control.contains(e));
    const public_context = publicContext === true && groupMode === true && stage === 'form' ?
      {headings:[...document.querySelectorAll('h1,h2')].filter(visible).slice(0,3).map(e=>clean(caption(e),'Сторінка'))} : null;
    // Bind dispatch to source captions and controls, independently of our own
    // redaction changes as values are filled. Snapshot disclosure changes still
    // invalidate composition, but cannot invalidate a locked local transaction.
    const structure = value => JSON.parse(JSON.stringify(value, (key,item)=>key === 'label' ? undefined : item));
    const signature = JSON.stringify([location.href, stage, structure(groupMode === true ? groups : fields), structure(groupMode === true ? [] : actions), confirmable,
      public_context ? [...document.querySelectorAll('h1,h2')].filter(visible).slice(0,3).map(caption) : null,
      [...document.querySelectorAll('input,textarea,select,option,button,form,[role=button],a[href],[role=link]')]
      .filter(e => stage !== 'form' || tracked.some(group => belongs(e, group))).map(e =>
        [id(e),e.tagName,e.id,e.name,e.type,visible(e),allButtons.includes(e) ? null : !!e.disabled,!!e.readOnly,e.required,
         e.autocomplete,e.maxLength,e.multiple,formOf(e) ? id(formOf(e)) : null,
         e.tagName === 'FORM' ? [e.action,e.method,e.target,e.getAttribute('action'),e.getAttribute('method')] : null,
         e.getAttribute('formaction'),e.getAttribute('formmethod'),e.getAttribute('formtarget'),
         e.getAttribute('href'),e.getAttribute('target'),e.hasAttribute('download'),e.getAttribute('role'),e.getAttribute('tabindex'),
         e.type === 'hidden' || e.tagName === 'OPTION' ? e.value : null,
         ['INPUT','TEXTAREA','SELECT'].includes(e.tagName) ? captionFor(e) : null,
         allButtons.includes(e) ? [e.innerText,e.getAttribute('aria-label'),e.type === 'submit' ? e.value : null] : null,
         e.tagName === 'OPTION' ? e.label : null])]);
    const disclosureSignature = JSON.stringify([groups,fields,actions,public_context]);
    if (signature !== state.signature || (operation !== 'submit' && disclosureSignature !== state.disclosureSignature)) state.revision++;
    state.signature = signature;
    state.disclosureSignature = disclosureSignature;
    return {stage,fields,actions,selected,buttons,groups,confirmable,public_context,revision:state.document+':'+state.revision};
  }
  const initial = inspect();
  const result = s => ({stage:s.stage,fields:s.fields,actions:s.actions,revision:s.revision,confirmable:s.confirmable,origin:location.origin,
    ...(groupMode === true ? {groups:s.groups} : {}), ...(s.public_context ? {public_context:s.public_context} : {})});
  if (operation === 'snapshot') return result(initial);
  let dispatched = false;
  const reject = () => ({accepted:false,code:dispatched ? 'submit_uncertain' : 'stale_form'});
  if (operation !== 'submit' || initial.stage !== 'form' || initial.revision !== revision ||
      !initial.actions.some(a=>a.id===action) || Object.keys(values).sort().join(',') !== initial.fields.map(f=>f.name).sort().join(',')) return reject();
  const selectedButton = initial.buttons.find(e => 'a'+id(e) === action);
  let installedNativeGuard = false;
  if (scriptOnlyForm(formOf(selectedButton)) && state.nativeFormsDisabled !== true) {
    // A default-GET credential form may be handled by delegated JavaScript.
    // Enforce browser CSP before any fill, preserving those handlers but
    // blocking every native form submission, including prototype.submit().
    // This restriction remains document-wide even if the meta is removed;
    // only a new document restores native POST submission capability.
    if (!document.head) return reject();
    document.addEventListener('securitypolicyviolation', event => {
      // Keep only an enum-like outcome: blockedURI can contain private input.
      if (event.effectiveDirective === 'form-action' && event.disposition === 'enforce') {
        state.nativeFormBlocked = true;
      }
    });
    const policy = document.createElement('meta');
    policy.httpEquiv = 'Content-Security-Policy';
    policy.content = "form-action 'none'";
    document.head.append(policy);
    if (!policy.isConnected || policy.parentNode !== document.head ||
        policy.httpEquiv !== 'Content-Security-Policy' || policy.content !== "form-action 'none'") return reject();
    installedNativeGuard = true;
  }
  try {
  for (const field of initial.fields) {
    if (typeof values[field.name] === 'string') state.secrets.push(values[field.name]);
  }
  state.secrets = [...new Set(state.secrets)].slice(-128);
  for (const field of initial.fields) {
    const current = inspect();
    if (current.revision !== revision || location.origin !== expectedOrigin) return reject();
    const e = current.selected.find(e=>'f'+id(e)===field.name);
    if (!e || !safeForm(formOf(e))) return reject();
    if (field.type === 'select') {
      const option = [...e.options].find(o=>'o'+id(o)===values[field.name] && !o.disabled);
      if (!option) return reject();
      dispatched = true;
      e.selectedIndex = option.index;
    } else if (field.type === 'checkbox') {
      dispatched = true;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'checked').set.call(e,values[field.name]);
    } else {
      dispatched = true;
      Object.getOwnPropertyDescriptor(e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,'value').set.call(e,values[field.name]);
    }
    e.dispatchEvent(new Event('input',{bubbles:true}));
    e.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise(resolve=>requestAnimationFrame(resolve));
  }
  const current = inspect(), button = current.buttons.find(e=>'a'+id(e)===action);
  if (current.revision !== revision || location.origin !== expectedOrigin || !button || button.disabled || button.getAttribute('aria-disabled') === 'true' || !safeForm(formOf(button),button)) return reject();
  state.signature=''; state.revision++;
  button.click();
  return {accepted:true};
  } finally {
    // The browser guard already applies during filling. Publish its effect on
    // subsequent candidate collection after this locked transaction, so our
    // own policy change cannot invalidate an otherwise unchanged selection.
    if (installedNativeGuard) state.nativeFormsDisabled = true;
  }
}"""


class WebsiteForms(WebsiteBrowser):
    COMPOSITION_LIMIT = 12
    form_schema = 'website-v1'

    def __init__(self, url, *, playwright_factory=None, resolver=None, composer=None, static_resources=False,
                 resource_hosts=None, auth_hosts=None):
        if type(static_resources) is not bool:
            raise ValueError('An explicit static resource policy flag is required.')
        super().__init__('google', playwright_factory=playwright_factory)
        self.initial_url = validated_url(url)
        parsed = urlsplit(self.initial_url)
        self.provider = 'website'
        self.destination = parsed.scheme + '://' + parsed.netloc
        self._document_origin = self.destination
        self.config = {'url': self.initial_url, 'origins': {self.destination}}
        self._resolver = resolver
        if resource_hosts is None:
            self._resources = StaticWebsiteResources(self.destination, resolver=resolver) if static_resources else None
        else:
            policy = validate_static_resources({self.destination: resource_hosts}, tiktok=static_resources)
            self._resources = StaticWebsiteResources(self.destination, hosts=policy[self.destination], resolver=resolver)
        self._auth_hosts = WebsiteAuthHosts(self.destination, auth_hosts, resolver) if auth_hosts else None
        self._pinned_address = None
        self._submitted = False
        self._redactions = []  # private process memory only; cleared with the lease
        self._text_redactions = []  # submitted free text only; private option captions
        # Monotonic for this browser's lifetime, including rejected/uncertain
        # submissions. This Python flag, not page JavaScript, owns disclosure.
        self._human_values_entered = False
        self._composer = composer
        self._scene_enabled = getattr(composer, 'scene_capable', False) is True
        self._scene_history = []
        self._composition_revision = self._composition_plan = self._composition_observation = None
        self._compositions = {}
        self._clarifications = {}  # Private, bounded by the same DOM-revision cap.
        self._clarification_overflow = False
        self._document_request = None
        self._document_status = None
        self._document_pending = self._document_failed = False
        self._document_error = None
        self._block_reason = None
        self._known_provider = ('google' if parsed.hostname in {'accounts.google.com', 'myaccount.google.com'}
                                else 'instagram' if parsed.hostname == 'www.instagram.com' else None)

    @property
    def document_origin(self):
        return self._document_origin

    def _before_private_dispatch(self, state, action):
        pass

    def _private_dispatch_rejected(self):
        pass

    async def start(self):
        try:
            host = urlsplit(self.initial_url).hostname
            resolver = self._resolver or asyncio.get_running_loop().getaddrinfo
            records = await asyncio.wait_for(resolver(host, 443, type=socket.SOCK_STREAM), 10)
            addresses = {record[4][0] for record in records}
            if not addresses or not all(public_address(address) for address in addresses):
                raise ValueError
            self._pinned_address = sorted(addresses, key=lambda value: (':' in value, value))[0]
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError('Website address could not be verified as public.') from None
        if self._resources is not None:
            await self._resources.prepare()
        if self._auth_hosts is not None:
            await self._auth_hosts.prepare()
        await super().start()

    def _launch_options(self):
        if self._pinned_address is None:
            raise ValueError('Website address is not pinned.')
        options = super()._launch_options()
        host = urlsplit(self.initial_url).hostname
        address = '[' + self._pinned_address + ']' if ':' in self._pinned_address else self._pinned_address
        options['args'] += ['--no-proxy-server', '--disable-quic', '--dns-prefetch-disable',
                            '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                            '--host-resolver-rules=MAP ' + host + ' ' + address + ', MAP * ~NOTFOUND']
        return options

    async def _configure_context(self):
        # WebSocket connections are not covered by Playwright's HTTP route.
        await self.context.route_web_socket('**/*', lambda websocket: websocket.close())
        self.context.on('request', self._document_requested)
        self.context.on('response', self._document_responded)
        self.context.on('requestfinished', self._document_finished)
        self.context.on('requestfailed', self._document_request_failed)

    def _document_requested(self, request):
        if self.page is not None and request.is_navigation_request() and request.frame == self.page.main_frame:
            self._document_request = request
            self._document_status = None
            self._document_pending = True

    def _document_responded(self, response):
        if response.request == self._document_request:
            self._document_status = response.status
            if response.status >= 400:
                self._document_failed = True
                self._document_error = 'http_error'

    def _document_finished(self, request):
        if request == self._document_request:
            status = self._document_status
            if status is not None and 200 <= status < 300 and status not in {204, 205}:
                # A redirect or an unrelated successful asset cannot clear a
                # failed document. Wait for this document's completed response.
                self._document_failed = self._document_pending = False
                self._document_error = None
            elif status is not None and status >= 400:
                self._document_pending = False

    def _document_request_failed(self, request):
        if request == self._document_request:
            self._document_failed = True
            self._document_pending = False
            self._document_error = 'network_error'

    def _block(self, reason):
        self._blocked = True
        # An aborted request can emit requestfailed afterwards. Preserve the
        # locally observed policy decision, never call our own abort a refusal.
        if self._block_reason is None or self._block_reason == 'route_error':
            self._block_reason = reason

    @staticmethod
    def _blocked_state(reason):
        return {'stage': 'provider_blocked', 'revision': 'unavailable', 'fields': [],
                'actions': [], 'groups': [], 'confirmable': False, '_block_reason': reason}

    def _state_message(self, state):
        if state['stage'] == 'provider_blocked':
            # Normal DOM observations reach this stage only through the fixed
            # rejection detector. Transport/policy exits always tag their cause.
            fallback = 'page_rejection' if self.provider == 'website' and not self._blocked else 'unknown'
            return BLOCK_MESSAGES.get(state.get('_block_reason', fallback), BLOCK_MESSAGES['unknown'])
        return state.get('_scene_message') or MESSAGES[state['stage']]

    def _document_guard(self):
        if self._blocked:
            return self._blocked_state(self._block_reason or 'unknown')
        if self._document_failed:
            return self._blocked_state(self._document_error or 'unknown')
        if self._document_pending:
            raise RecoverableFormError('navigation_pending')
        return None

    async def _close_popup(self, page):
        if page != self.page:
            self._block('popup')
            await super()._close_popup(page)

    async def _route(self, route):
        try:
            request = route.request
            parsed = urlsplit(request.url)
            origin = parsed.scheme + '://' + parsed.netloc
            same = origin == self.document_origin
            navigation = request.is_navigation_request()
            main = navigation and request.frame == self.page.main_frame
            if not same and not navigation and self._auth_hosts is not None:
                frame = urlsplit(request.frame.url)
                if frame.scheme + '://' + frame.netloc == self.destination and await self._auth_hosts.handle(route):
                    return
            if not same and not navigation and self._resources is not None and await self._resources.handle(route):
                return
            # The CDP guard authorizes every document hop before this handler.
            # Root identity remains fixed; private approval grants only the
            # exact document origin, never arbitrary subresource initiators.
            approved_document = (main and getattr(self, '_navigation', None) is not None
                                 and origin in self._approved_origins)
            if (not same and not approved_document) or (navigation and not main) or parsed.username or parsed.password or parsed.port not in (None, 443):
                if main:
                    self._block('origin_policy')
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            self._block('route_error')
            try:
                await route.abort()
            except Exception:
                pass

    async def _inspect(self):
        unavailable = self._document_guard()
        if unavailable is not None:
            return unavailable
        if self.page is None or self._blocked:
            return self._blocked_state(self._block_reason or 'unknown')
        parsed = urlsplit(self.page.url)
        if parsed.scheme + '://' + parsed.netloc != self.document_origin:
            self._block('origin_policy')
            return self._blocked_state(self._block_reason)
        if self._scene_enabled:
            return await self._inspect_scene()
        state = await self.page.evaluate(GENERIC_SCRIPT, {'stateKey': self._state_key, 'expectedOrigin': self.document_origin,
                                                       'allowOriginApproval': getattr(self, '_navigation', None) is not None,
                                                       'maxSelectOptions': MAX_SELECT_OPTIONS if getattr(self, 'form_schema', None) == 'website-v2' else 32,
                                                       'operation': 'snapshot', 'mayConfirm': self._submitted,
                                                       'redactions': self._redactions, 'credentialValues': self._credential_values,
                                                       'groupMode': self._composer is not None,
                                                       'publicContext': not self._human_values_entered})
        # Recheck after awaiting page code. Page state is never the authority
        # for enabling external text disclosure, including on cancellation.
        if self._human_values_entered and isinstance(state, dict):
            state.pop('public_context', None)
        if (not isinstance(state, dict) or state.get('stage') not in MESSAGES or state.get('origin') != self.document_origin
                or not re.fullmatch(r'[a-f0-9-]{36}:\d{1,10}', str(state.get('revision', '')))
                or type(state.get('confirmable')) is not bool
                or not isinstance(state.get('fields'), list) or len(state['fields']) > 16
                or not isinstance(state.get('actions'), list) or len(state['actions']) > 8):
            raise ValueError('Website form unavailable.')
        for field in state['fields']:
            if (not isinstance(field, dict) or field.get('type') not in FIELD_TYPES
                    or not re.fullmatch(r'f\d{1,8}', str(field.get('name', '')))
                    or not isinstance(field.get('label'), str) or not 0 < len(field['label']) <= 120
                    or type(field.get('max_length')) is not int or not 0 < field['max_length'] <= FIELD_TYPES[field['type']]):
                raise ValueError('Website form unavailable.')
            keys = {'name', 'type', 'label', 'max_length'} | ({'options'} if field['type'] == 'select' else set())
            if set(field) != keys:
                raise ValueError('Website form unavailable.')
            if field['type'] == 'select' and (not isinstance(field['options'], list) or not 0 < len(field['options']) <= 32
                    or any(set(option) != {'value', 'label'} or not re.fullmatch(r'o\d{1,8}', str(option['value']))
                           or not isinstance(option['label'], str) or not 0 < len(option['label']) <= 120 for option in field['options'])):
                raise ValueError('Website form unavailable.')
        if any(set(action) != {'id', 'label'} or not re.fullmatch(r'a\d{1,8}', str(action['id']))
               or not isinstance(action['label'], str) or not 0 < len(action['label']) <= 120 for action in state['actions']):
            raise ValueError('Website form unavailable.')
        if self._known_provider:
            known = await self.page.evaluate(FORM_SCRIPT, {'provider': self._known_provider,
                'stateKey': self._state_key + '_known', 'operation': 'snapshot'})
            if known.get('stage') == 'authenticated' and known.get('origin') == self.document_origin:
                state.update(stage='authenticated', fields=[], actions=[], groups=[], confirmable=False)
        if state['stage'] != 'form':
            state.pop('public_context', None)
        if self._composer is not None:
            if not isinstance(state.get('groups'), list) or len(state['groups']) > 8:
                raise ValueError('Website form unavailable.')
            if state['stage'] == 'form':
                try:
                    self._group_observation(state)
                except ValueError:
                    # Per-group DOM limits do not imply the catalog's total
                    # metadata limit. An oversized layout cannot be composed.
                    state.update(stage='unsupported_layout', fields=[], actions=[], groups=[], confirmable=False)
        return self._document_guard() or state

    async def _inspect_scene(self):
        state = await self.page.evaluate(SCENE_SCRIPT, {
            'stateKey': self._state_key, 'expectedOrigin': self.document_origin,
            'allowOriginApproval': getattr(self, '_navigation', None) is not None,
            'maxSelectOptions': MAX_SELECT_OPTIONS if getattr(self, 'form_schema', None) == 'website-v2' else 32,
            'operation': 'snapshot', 'mayConfirm': self._submitted,
            'redactions': self._redactions, 'textRedactions': self._text_redactions,
            'credentialValues': self._credential_values,
            'afterInput': self._human_values_entered})
        if (type(state) is not dict or state.get('stage') not in MESSAGES
                or state.get('origin') != self.document_origin
                or not re.fullmatch(r'[a-f0-9-]{36}:\d{1,10}', str(state.get('revision', '')))
                or type(state.get('confirmable')) is not bool):
            raise ValueError('Website form unavailable.')
        raw_scene = state['scene']
        if self._scene_history:
            raw_scene = dict(raw_scene, history=deepcopy(self._scene_history))
        scene = validate_scene(sanitize_scene(raw_scene, self._redactions, self._credential_values,
            option_redactions=self._text_redactions))
        if (scene['destination'] != self.document_origin or scene['revision'] != state['revision']
                or scene['after_input'] != self._human_values_entered):
            raise RecoverableFormError('stale_form')
        state.update(fields=[], actions=[], groups=[], scene=scene)
        if self._known_provider:
            known = await self.page.evaluate(FORM_SCRIPT, {'provider': self._known_provider,
                'stateKey': self._state_key + '_known', 'operation': 'snapshot'})
            if known.get('stage') == 'authenticated' and known.get('origin') == self.document_origin:
                state.update(stage='authenticated', confirmable=False)
        if (self.provider == 'website' and state['stage'] == 'form' and state['confirmable']
                and not scene['controls'] and not scene['overflow']['controls']):
            # A normal landing page has navigation actions. Completion remains
            # a separate human assertion, independent of a login-action model.
            state.update(stage='review', fields=[], actions=[])
        return self._document_guard() or state

    def _compile(self, plan, observation):
        return (compile_scene if self._scene_enabled else compile_plan)(plan, observation)

    def _group_observation(self, state):
        if self._scene_enabled:
            scene = sanitize_scene(state['scene'], self._redactions, self._credential_values,
                option_redactions=self._text_redactions)
            # Per-action surface bindings belong to the website-v2 private
            # protocol. Legacy composition/clarification keeps one-owner plans
            # and the same projection for inference, caching and freshness.
            if self.form_schema != 'website-v2':
                scene.pop('surfaces', None)
            return validate_scene(scene)
        # The catalog describes widgets, not the lease's authentication identity.
        observation = {'provider': 'website', 'destination': self.destination, 'stage': state['stage'],
                       'revision': state['revision'], 'groups': deepcopy(state['groups']),
                       'message': {'kind': 'status', 'text': MESSAGES[state['stage']]}}
        if not self._human_values_entered and state['stage'] == 'form' and 'public_context' in state:
            observation['public_context'] = deepcopy(state['public_context'])
        # Structural validation happens before handing anything to the private
        # composer. The composer owns any further filtering for external models.
        model_context(observation)
        return observation

    def _selected_composition(self, state):
        if self._composition_revision != state['revision']:
            raise RecoverableFormError('stale_form')
        if self._composition_plan is None:
            pending = self._clarifications.get(state['revision'])
            if pending is not None and pending['status'] == 'awaiting':
                if self._group_observation(state) != pending['observation']:
                    raise RecoverableFormError('stale_form')
                return dict(state, stage='clarification', revision=pending['ui_revision'],
                    fields=[{'name': 'f0', 'type': 'select', 'label': 'Яку дію потрібно підготувати?',
                        'max_length': 64, 'options': deepcopy(pending['options'])}],
                    actions=[{'id': 'a0', 'label': 'Показати поля'}], confirmable=False,
                    _scene_message='Обери, для якої дії показати поля. Цей вибір не натискає кнопку на сайті й не надсилає дані.')
            raise RecoverableFormError('composition_unavailable')
        observation = self._group_observation(state)
        if observation != self._composition_observation:
            raise RecoverableFormError('stale_form')
        compiled = self._compile(self._composition_plan, observation)
        if self._scene_enabled:
            return dict(state, fields=compiled['fields'], actions=compiled['actions'],
                        _scene_plan=self._composition_plan, _scene_message=compiled['message'])
        return dict(state, fields=compiled['fields'], actions=compiled['actions'],
                    _selected_group_id=self._composition_plan['group_id'])

    async def _compose(self, state):
        if self._composition_revision != state['revision']:
            # Mark the attempt before awaiting it. Keep bounded outcomes even
            # when a prior document revision is restored by browser history.
            self._composition_revision = state['revision']
            self._composition_plan = self._composition_observation = None
            if state['revision'] in self._compositions:
                cached = self._compositions[state['revision']]
                if cached is not None:
                    self._composition_plan, self._composition_observation = cached
                return self._selected_composition(state)
            if len(self._compositions) >= self.COMPOSITION_LIMIT:
                raise RecoverableFormError('composition_unavailable')
            self._compositions[state['revision']] = None
            try:
                observation = self._group_observation(state)
                result = await self._composer.plan(deepcopy(observation))
                if self._human_values_entered and 'public_context' in observation:
                    # A value submission cancelled while waiting for this lock
                    # can close disclosure during inference. Never repopulate
                    # the public cache after that irreversible transition.
                    raise ValueError
                if type(result) is not dict or 'plan' not in result:
                    raise ValueError
                self._compile(result['plan'], observation)
                self._composition_plan = deepcopy(result['plan'])
                self._composition_observation = observation
                self._compositions[state['revision']] = (self._composition_plan, observation)
            except asyncio.CancelledError:
                raise
            except WebsitePlannerError as error:
                anchors = (_eligible_anchors(observation) if self._scene_enabled
                    and is_anchor_abstention(observation, error) else [])
                self._clarification_overflow = self._clarification_overflow or len(anchors) > 32
                if not 1 <= len(anchors) <= 32:
                    raise RecoverableFormError('composition_unavailable') from None
                # An explicit model abstention offers clarification, never an
                # automatic choice. Excess candidates are not silently dropped.
                self._clarifications[state['revision']] = {
                    'ui_revision': str(uuid.uuid4()) + ':1', 'observation': observation,
                    'choices': {'o' + str(i + 1): item['ref'] for i, item in enumerate(anchors)},
                    'options': [{'value': 'o' + str(i + 1), 'label': _widget_label(item['label'])}
                                for i, item in enumerate(anchors)],
                    'status': 'awaiting', 'chosen': None,
                    'abstention': deepcopy(getattr(error, 'decision', None)),
                }
            except Exception:
                raise RecoverableFormError('composition_unavailable') from None
            # The page can change while the model is deciding. Re-read only;
            # the next snapshot may compose the new revision, never this input.
            fresh = await self._observe()
            if fresh['revision'] != state['revision'] or fresh['stage'] != state['stage']:
                raise RecoverableFormError('stale_form')
            state = fresh
        return self._selected_composition(state)

    async def snapshot(self):
        async with self._lock:
            try:
                state = await self._observe()
                if self._composer is not None and state['stage'] == 'form':
                    # Keep paid inference outside _observe's short read timeout.
                    state = await self._compose(state)
                return {'provider': self.provider, 'destination': self.destination, 'stage': state['stage'],
                        'form_schema': self.form_schema,
                        'revision': state['revision'], 'fields': state['fields'],
                        'actions': state['actions'] + ([{'id': 'confirm', 'label': 'Підтвердити завершення'}] if state.get('confirmable') else []),
                        'message': self._state_message(state)}
            except asyncio.CancelledError:
                raise
            except RecoverableFormError:
                raise
            except Exception:
                raise ValueError('Website form unavailable.') from None

    async def choose_action(self, revision, choice):
        """Clarify UI intent only. No DOM input, click or authentication effect."""
        async with self._lock:
            if not self._scene_enabled or type(revision) is not str or type(choice) is not str:
                raise RecoverableFormError('stale_form')
            entry = next(((native, pending) for native, pending in self._clarifications.items()
                          if pending['ui_revision'] == revision), None)
            if entry is None:
                raise RecoverableFormError('stale_form')
            native_revision, pending = entry
            state = await self._observe()
            if (state['revision'] != native_revision or state['stage'] != 'form'
                    or self._group_observation(state) != pending['observation']
                    or choice not in pending['choices']):
                raise RecoverableFormError('stale_form')
            if pending['status'] == 'succeeded' and pending['chosen'] == choice:
                return  # Idempotent network duplicate; no model request.
            if pending['status'] != 'awaiting':
                raise RecoverableFormError('composition_unavailable')
            # Consume before await: cancellation, timeout or uncertain transport
            # must not become a paid retry through polling or a second click.
            pending['status'], pending['chosen'] = 'failed', choice
            try:
                result = await self._composer.plan_for_anchor(
                    deepcopy(pending['observation']), pending['choices'][choice])
                if (type(result) is not dict or type(result.get('plan')) is not dict
                        or result['plan'].get('anchor') != pending['choices'][choice]):
                    raise ValueError
                self._compile(result['plan'], pending['observation'])
                fresh = await self._observe()
                if (fresh['revision'] != native_revision or fresh['stage'] != 'form'
                        or self._group_observation(fresh) != pending['observation']):
                    raise RecoverableFormError('stale_form')
                self._composition_revision = native_revision
                self._composition_plan = deepcopy(result['plan'])
                self._composition_observation = pending['observation']
                self._compositions[native_revision] = (self._composition_plan, self._composition_observation)
                pending['status'] = 'succeeded'
            except asyncio.CancelledError:
                raise
            except RecoverableFormError:
                raise
            except Exception:
                raise RecoverableFormError('composition_unavailable') from None

    async def submit(self, revision, action, values):
        try:
            async with self._lock:
                try:
                    state = await self._observe()
                    if state['revision'] != revision:
                        raise RecoverableFormError('stale_form')
                    if self._composer is not None:
                        state = self._selected_composition(state)
                    chosen = next((item for item in state['actions'] if item['id'] == action), None)
                    if chosen is not None and 'field_refs' in chosen:
                        # A display surface can contain several native forms.
                        # Only this human-chosen action's broker-owned bindings
                        # may enter the private dispatch transaction.
                        if chosen.get('available') is not True:
                            raise ValueError
                        field_map = {field['name']: field for field in state['fields']}
                        state = dict(state, fields=[field_map[ref] for ref in chosen['field_refs']])
                    if (state['stage'] != 'form' or not isinstance(values, dict)
                            or action not in {item['id'] for item in state['actions']}
                            or set(values) != {field['name'] for field in state['fields']}):
                        raise ValueError
                    for field in state['fields']:
                        value = values[field['name']]
                        if field['type'] == 'checkbox':
                            if type(value) is not bool:
                                raise ValueError
                        elif (not isinstance(value, str) or len(value) > field['max_length'] or '\x00' in value
                              or (field['type'] == 'select' and value not in {option['value'] for option in field['options']})):
                            raise ValueError
                    option_redactions = []
                    if self._scene_enabled and any(field['type'] in {'select', 'checkbox'} for field in state['fields']):
                        private = await self.page.evaluate(SCENE_SCRIPT, {
                            'stateKey': self._state_key, 'expectedOrigin': self.document_origin,
                            'allowOriginApproval': getattr(self, '_navigation', None) is not None,
                            'maxSelectOptions': MAX_SELECT_OPTIONS if getattr(self, 'form_schema', None) == 'website-v2' else 32,
                            'operation': 'private_redactions', 'revision': revision, 'action': action,
                            'values': values, 'plan': state['_scene_plan'], 'mayConfirm': self._submitted,
                            'redactions': self._redactions, 'textRedactions': self._text_redactions,
                            'credentialValues': self._credential_values,
                            'afterInput': self._human_values_entered})
                        if private == {'accepted': False, 'code': 'stale_form'}:
                            raise RecoverableFormError('stale_form')
                        if (type(private) is not dict or set(private) != {'redactions'}
                                or type(private['redactions']) is not list or len(private['redactions']) > 2 * MAX_CONTROLS
                                or any(type(value) is not str or not 0 < len(value) <= 4096 for value in private['redactions'])):
                            raise ValueError
                        option_redactions = private['redactions']
                    redactions = list(dict.fromkeys(self._redactions + option_redactions + [value for value in values.values()
                        if isinstance(value, str) and value]))
                    if len(redactions) > 128:
                        raise ValueError
                    text_redactions = list(dict.fromkeys(self._text_redactions + [values[field['name']]
                        for field in state['fields'] if field['type'] in {'text', 'email', 'password', 'otp'}
                        and values[field['name']]]))
                    if len(text_redactions) > 128:
                        raise ValueError
                    self._redactions = redactions
                    self._text_redactions = text_redactions
                    credentials = list(dict.fromkeys(self._credential_values + [values[field['name']] for field in state['fields']
                        if field['type'] in {'password', 'otp'} and values[field['name']]]))
                    if len(credentials) > 128:
                        raise ValueError
                    self._credential_values = credentials
                except asyncio.CancelledError:
                    raise
                except RecoverableFormError:
                    raise
                except Exception:
                    raise ValueError('Website input rejected.') from None
                finally:
                    self._close_text_disclosure(values)
                try:
                    self._before_private_dispatch(state, action)
                    result = await self.page.evaluate(SCENE_SCRIPT if self._scene_enabled else GENERIC_SCRIPT, {'stateKey': self._state_key, 'expectedOrigin': self.document_origin,
                        'allowOriginApproval': getattr(self, '_navigation', None) is not None,
                        'maxSelectOptions': MAX_SELECT_OPTIONS if getattr(self, 'form_schema', None) == 'website-v2' else 32,
                        'operation': 'submit', 'revision': revision, 'action': action, 'values': values,
                        'plan': state.get('_scene_plan'), 'afterInput': self._human_values_entered,
                        'mayConfirm': self._submitted, 'redactions': self._redactions,
                        'textRedactions': self._text_redactions, 'credentialValues': self._credential_values,
                        'groupMode': self._composer is not None, 'groupId': state.get('_selected_group_id'),
                        # This captured shape is used only for local freshness
                        # comparisons during dispatch; the result contains no text.
                        'publicContext': 'public_context' in state})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise SubmitUncertainError() from None
                if result == {'accepted': False, 'code': 'stale_form'}:
                    self._private_dispatch_rejected()
                    raise RecoverableFormError('stale_form')
                if result != {'accepted': True}:
                    raise SubmitUncertainError()
                self._submitted = self._submitted or bool(values)
                if self._scene_enabled:
                    chosen = next(item for item in state['actions'] if item['id'] == action)
                    self._scene_history = (self._scene_history + [{'action': chosen['label'],
                        'field_types': [field['type'] for field in state['fields']]}])[-3:]
        except asyncio.CancelledError:
            # Cancellation while waiting for the lock must close disclosure too.
            self._close_text_disclosure(values)
            raise

    def _close_text_disclosure(self, values):
        if isinstance(values, dict) and values and self._resources is not None:
            # Also burn the anonymous path window for rejected/cancelled input.
            # This helper runs before dispatch and on lock-wait cancellation.
            self._resources.freeze()
        if isinstance(values, dict) and values and not self._human_values_entered:
            self._human_values_entered = True
            if self._scene_enabled:
                # V2 retains redacted semantic evidence after input. The next
                # observation changes after_input and invalidates composition.
                return
            self._composition_revision = self._composition_plan = self._composition_observation = None
            # Preserve the paid-attempt bound but discard cached text.
            for cached_revision, cached in list(self._compositions.items()):
                if cached is not None and 'public_context' in cached[1]:
                    self._compositions[cached_revision] = None

    async def confirmable(self, revision):
        async with self._lock:
            try:
                state = await self._observe()
                return state.get('confirmable') is True and state['revision'] == revision
            except asyncio.CancelledError:
                raise
            except RecoverableFormError:
                raise
            except Exception:
                return False

    def diagnostics(self):
        return {'composition_revisions': len(self._compositions),
                'scene_catalog': self._scene_enabled,
                'clarification': {'status': self._clarifications.get(self._composition_revision, {}).get('status', 'none'),
                    'revisions': len(self._clarifications), 'candidate_overflow': self._clarification_overflow},
                'auth_network': self._auth_hosts.diagnostics() if self._auth_hosts is not None else None,
                'resources': self._resources.diagnostics() if self._resources is not None else None,
                'model': self._composer.diagnostics() if hasattr(self._composer, 'diagnostics') else None}

    async def close(self):
        try:
            await super().close()
        finally:
            try:
                if self._resources is not None:
                    await self._resources.close()
            finally:
                try:
                    if self._auth_hosts is not None:
                        await self._auth_hosts.close()
                finally:
                    self._redactions.clear()
                    self._text_redactions.clear()
                    self._scene_history.clear()
                    self._composition_revision = self._composition_plan = self._composition_observation = None
                    self._compositions.clear()
                    self._clarifications.clear()
                    self._clarification_overflow = False
                    self._document_request = self._document_status = None
                    self._document_pending = self._document_failed = False
                    self._document_error = self._block_reason = None


class ComposedProviderForms(WebsiteForms):
    """Dynamic private widgets under an existing, fixed provider policy.

    The read-only known adapter shares only the page, never starts another
    browser, and owns a separate DOM state key. Its positive account evidence
    remains the sole authentication authority. Composition cannot broaden the
    provider's origins, turn a redirect into login, or enable owner confirmation.
    """

    def __init__(self, provider, *, composer, playwright_factory=None):
        if provider not in PROVIDERS or composer is None:
            raise ValueError('A known provider and explicit composer are required.')
        super().__init__(PROVIDERS[provider]['url'], composer=composer,
                         playwright_factory=playwright_factory)
        self.provider = provider
        self.config = PROVIDERS[provider]
        self._known_provider = None  # Evidence is checked before the generic origin guard.
        self._evidence = WebsiteBrowser(provider)

    async def start(self):
        # Known provider policy already permits its fixed resources and evidence
        # redirect. Do not inherit the arbitrary-site single-host DNS pin.
        await WebsiteBrowser.start(self)

    def _launch_options(self):
        return WebsiteBrowser._launch_options(self)

    async def _route(self, route):
        await WebsiteBrowser._route(self, route)

    async def _inspect(self):
        unavailable = self._document_guard()
        if unavailable is not None:
            return unavailable
        self._evidence.page = self.page
        self._evidence._blocked = self._blocked
        known = await self._evidence._inspect()
        unavailable = self._document_guard()
        if unavailable is not None:
            return unavailable
        fixed = {'stage': known['stage'], 'revision': known['revision'], 'fields': [],
                 'actions': [], 'groups': [], 'confirmable': False}
        if known['stage'] in {'authenticated', 'provider_blocked', 'unsupported_challenge', 'device_approval'}:
            return fixed
        if known.get('dismiss'):
            # Preserve the existing fixed Instagram consent/dismiss policy.
            # a0 cannot collide with generic DOM handles, whose IDs start at 1.
            return dict(fixed, stage='form', _provider_prompt=True, actions=[{'id': 'a0',
                'label': 'Відхилити необов’язкові кукі' if known.get('cookies') else 'Не зараз'}])
        parsed = urlsplit(self.page.url)
        if parsed.scheme + '://' + parsed.netloc != self.destination:
            # E.g. myaccount.google.com without the required positive evidence.
            return dict(fixed, stage='unsupported_layout')
        state = await super()._inspect()
        state['confirmable'] = False
        if state['stage'] == 'review':
            state.update(stage='unsupported_layout', fields=[], actions=[], groups=[])
        return state

    async def _compose(self, state):
        if state.get('_provider_prompt'):
            return state
        return await super()._compose(state)

    def _selected_composition(self, state):
        if state.get('_provider_prompt'):
            return state
        return super()._selected_composition(state)

    async def verified(self):
        async with self._lock:
            # Read-only observation; a model result never participates in proof.
            return (await self._observe())['stage'] == 'authenticated'

    async def confirmable(self, revision):
        return False

    async def submit(self, revision, action, values):
        if action != 'a0':
            return await super().submit(revision, action, values)
        # This fixed action never accepts values, so no captured descriptor
        # needs to be preserved while burning a rejected submission's gate.
        self._close_text_disclosure(values)
        async with self._lock:
            try:
                state = await self._observe()
                if state['revision'] != revision:
                    raise RecoverableFormError('stale_form')
                if not state.get('_provider_prompt') or type(values) is not dict or values:
                    raise ValueError('Website input rejected.')
                await self._evidence.submit(revision, 'submit', {})
            finally:
                self._close_text_disclosure(values)

    async def close(self):
        try:
            await super().close()
        finally:
            self._evidence.page = None
