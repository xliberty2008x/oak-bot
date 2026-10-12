"""Native-agent page observation and private human forms on one broker lease.

The agent supplies grounded component plans, never human values or selectors.
Only the private renderer submits fields; observation and presentation do not
run a model, execute site actions, or establish authentication.
"""

import asyncio
from copy import deepcopy
import math
import re
import time
import uuid
from urllib.parse import urlsplit

from .signin_providers import RecoverableFormError, SubmitUncertainError
from .website_forms import WebsiteForms, validated_url
from .website_resources import AnonymousWebsiteResources
from .website_scene import (CATALOG, CATALOG_ID, CATALOG_VERSION, MAX_SELECT_OPTIONS,
                            compile_plan, model_context, validate_scene)
from .website_scene_dom import SCENE_SCRIPT


_STAGES = frozenset({'agent_working', 'form', 'review', 'authenticated', 'provider_blocked',
                     'unsupported_challenge', 'unsupported_layout', 'device_approval'})
_OUTCOMES = frozenset({'idle', 'submitted', 'stale', 'uncertain'})
_REVISION = re.compile(r'[A-Za-z0-9_-]{1,100}:[0-9]{1,10}')
_POST_ACTION_SECONDS = 2
_POST_ACTION_POLL_SECONDS = 0.1


class AgentProposalError(RecoverableFormError):
    """A rejected model proposal leaves the bound browser available to observe."""

    def __init__(self, code):
        if code not in {'invalid_plan', 'human_step_pending', 'navigation_requires_owner'}:
            raise ValueError('Invalid agent proposal diagnostic.')
        self.code = code
        ValueError.__init__(self, code)


def validate_agent_observation(value):
    """Validate/copy the complete model-facing projection, with no extra keys."""
    try:
        if (type(value) is not dict or set(value) != {'revision', 'stage', 'scene', 'progress'}
                or type(value['revision']) is not str or not _REVISION.fullmatch(value['revision'])
                or type(value['stage']) is not str or value['stage'] not in _STAGES):
            raise ValueError
        progress = value['progress']
        if (type(progress) is not dict or set(progress) != {'event_seq', 'outcome'}
                or type(progress['event_seq']) is not int or not 0 <= progress['event_seq'] <= 1_000_000
                or type(progress['outcome']) is not str or progress['outcome'] not in _OUTCOMES):
            raise ValueError
        scene = value['scene']
        if (type(scene) is not dict or set(scene) != {'catalog', 'stage', 'controls', 'actions',
                'texts', 'after_input', 'overflow'} | (set(scene) & {'history', 'surfaces'})
                or scene['catalog'] != CATALOG or type(scene['controls']) is not list):
            raise ValueError
        controls = []
        option_index = 0
        for field in scene['controls']:
            if type(field) is not dict or 'options' in field:
                raise ValueError
            control = deepcopy(field)
            control.pop('components')
            if control.get('type') == 'select':
                count = control.pop('option_count')
                limit = MAX_SELECT_OPTIONS if 'surfaces' in scene else 32
                if type(count) is not int or not 1 <= count <= limit:
                    raise ValueError
                control['options'] = [{'value': 'o' + str(option_index + i + 1), 'label': 'Option'}
                                      for i in range(count)]
                option_index += count
            controls.append(control)
        # The transport projection deliberately lacks origin and option captions.
        # Synthetic validation-only values never enter browser state or execution.
        full = {key: deepcopy(item) for key, item in scene.items() if key != 'catalog'}
        full.update(provider='website', destination='https://scene.invalid',
                    revision=value['revision'], controls=controls)
        if model_context(validate_scene(full)) != scene:
            raise ValueError
        return deepcopy(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('Invalid agent website observation.') from None


class _PrivatePlanMode:
    # Enables the existing scene validator/dispatch path without a model planner.
    scene_capable = True


# Inspect native ownership immediately before the existing single-dispatch
# transaction. Unsupported, hidden, disabled and read-only inputs also count
# within an actual form; null owners do not associate otherwise loose controls.
_AGENT_ACTION_SCRIPT = r"""async args => {
  const state = window[args.stateKey];
  if (!state) return {accepted:false,code:'stale_form'};
  const roots=[document], nodes=[];
  for(let i=0;i<roots.length&&i<32;i++) {
    nodes.push(...roots[i].querySelectorAll('*'));
    for(const node of roots[i].querySelectorAll('*')) if(node.shadowRoot)roots.push(node.shadowRoot);
  }
  const button=nodes.find(node=>'a'+state.nodes.get(node)===args.action);
  if(!button)return {accepted:false,code:'stale_form'};
  const link=button.tagName==='A'||button.getAttribute('role')==='link';
  const owner=node=>node.form||node.closest('form');
  const nativeForm=owner(button);
  if(!link&&nativeForm&&nodes.some(node=>node.matches('input,textarea,select')&&
      !['submit','button','reset'].includes(node.type)&&owner(node)===nativeForm))
    return {accepted:false,code:'navigation_requires_owner'};
  const dispatch = """ + SCENE_SCRIPT + r""";
  return await dispatch(args);
}"""


class WebsiteAgentForms(WebsiteForms):
    """One native agent controls navigation; one human controls private input."""

    form_schema = 'website-v2'

    def __init__(self, url, *, anonymous_static_resources=False, origin_approval=False,
                 navigation_seconds=300, navigation_factory=None, navigation_guard=None, **options):
        if type(anonymous_static_resources) is not bool:
            raise ValueError('Anonymous resource activation must be an explicit boolean.')
        if 'composer' in options:
            raise ValueError('Agent website forms do not accept a model composer.')
        super().__init__(url, composer=_PrivatePlanMode(), **options)
        if (type(origin_approval) is not bool or type(navigation_seconds) not in (int, float)
                or not math.isfinite(navigation_seconds) or not 0 < navigation_seconds <= 900):
            raise ValueError('Invalid private navigation capability.')
        self._navigation = None
        self._navigation_closed = False
        self._navigation_seconds = navigation_seconds
        self._navigation_waiting = asyncio.Event()
        self._initial_document_task = None
        self._approved_origins = {self.destination: None}
        self._pending_navigation = None
        self._document_post = None
        self._retained = False
        self._navigation_guard = (lambda: None) if navigation_guard is None else navigation_guard
        if not callable(self._navigation_guard):
            raise ValueError('Invalid private navigation authority.')
        if origin_approval:
            from .website_navigation_transport import WebsiteNavigationTransport, Limits
            factory = navigation_factory or WebsiteNavigationTransport
            self._navigation = factory(self._authorize_navigation,
                on_block=lambda reason: self._block('origin_policy'),
                limits=Limits(lease_seconds=navigation_seconds))
        if anonymous_static_resources:
            self._resources = AnonymousWebsiteResources(self.destination, resolver=self._resolver)
        self._agent_closed = False
        self._agent_act_uncertain = False
        self._agent_human_completed = False
        self._agent_human_uncertain = False
        self._agent_event_seq = 0
        self._agent_outcome = 'idle'
        self._agent_ui_document = self._agent_ui_prefix = None
        self._agent_ui_sequence = 0
        self._agent_ui_signature = self._agent_ui_current = None

    async def start(self):
        if self._navigation is not None:
            await self._navigation.start()
        try:
            await super().start()
        except BaseException:
            if self._navigation is not None:
                await self._navigation.close()
            raise

    def _launch_options(self):
        options = super()._launch_options()
        if self._navigation is not None:
            options['args'] = [arg for arg in options['args']
                if arg != '--no-proxy-server' and not arg.startswith('--host-resolver-rules=')]
            options['args'] += self._navigation.browser_args()
            options['proxy'] = self._navigation.browser_proxy()
        return options

    async def _configure_page(self):
        await super()._configure_page()
        if self._navigation is not None:
            await self._navigation.attach(self.context, self.page)
            self.page.on('framenavigated', self._document_committed)

    async def _initial_navigation(self):
        if self._navigation is None:
            return await super()._initial_navigation()
        # An initial redirect can itself need consent. Do not keep attachment
        # waiting for a human who cannot see the private page until it returns.
        task = self._initial_document_task = asyncio.create_task(self.page.goto(
            self.config['url'], wait_until='domcontentloaded',
            timeout=self._navigation_seconds * 1000))
        def finished(result):
            if not result.cancelled() and result.exception() is not None and not self._navigation_closed:
                self._block('network_error')
        task.add_done_callback(finished)
        waiting = asyncio.create_task(self._navigation_waiting.wait())
        try:
            done, _ = await asyncio.wait((task, waiting), timeout=30,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
            elif waiting not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise ValueError('Website navigation did not become ready.')
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    @staticmethod
    def _navigation_origin(url):
        if (type(url) is not str or not 0 < len(url) <= 8192 or '\\' in url
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError('Invalid private navigation.')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not parsed.hostname):
            raise ValueError('Invalid private navigation.')
        # Validate only the origin. OAuth paths/query parameters remain private
        # and never enter an origin grant, error, model observation or log.
        origin = validated_url('https://' + parsed.netloc).rstrip('/')
        from .website_navigation_transport import canonical_host
        canonical_host(urlsplit(origin).hostname)
        return origin

    def _document_committed(self, frame):
        if frame != self.page.main_frame:
            return
        try:
            origin = self._navigation_origin(frame.url)
        except ValueError:
            self._block('origin_policy')
            return
        if origin not in self._approved_origins or self._navigation_closed:
            self._block('origin_policy')
            return
        if origin != self._document_origin:
            self._document_origin = origin
            self._clear_plan()
            if self._resources is not None:
                # Preserve cumulative quotas and the irreversible input freeze;
                # only the explicitly approved document's CORS recipient moves.
                self._resources.origin = origin

    async def _authorize_navigation(self, request):
        self._navigation_guard()
        if self._navigation_closed or self._blocked or not request.main_frame:
            raise ValueError('Navigation is unavailable.')
        origin = self._navigation_origin(request.url)
        if request.method not in {'GET', 'HEAD', 'POST'}:
            raise ValueError('Navigation method is unavailable.')
        if request.method == 'POST':
            permit, self._document_post = self._document_post, None
            if (request.redirected_from is not None or origin != self.document_origin
                    or (not self._retained and
                        (permit is None or permit[0] != origin or time.monotonic() >= permit[1]))):
                raise ValueError('Navigation requires private input.')
        if self._retained and origin != self.destination:
            raise ValueError('Navigation is outside the retained task.')
        if origin in self._approved_origins:
            pin = self._approved_origins[origin]
            if pin is None:
                pin = self._pinned_address
                if pin is None:
                    raise ValueError('Navigation is unavailable.')
                self._approved_origins[origin] = pin
            return pin
        if request.method not in {'GET', 'HEAD'} or len(self._approved_origins) >= 4 or self._pending_navigation is not None:
            raise ValueError('Navigation requires private approval.')
        pending = {'origin': origin, 'revision': 'approval-' + str(uuid.uuid4()) + ':0',
                   'future': asyncio.get_running_loop().create_future(), 'consumed': False}
        self._pending_navigation = pending
        self._navigation_waiting.set()
        self._clear_plan()
        try:
            pin = await pending['future']
            self._navigation_guard()
            if self._navigation_closed or self._blocked or self._approved_origins.get(origin) != pin:
                raise ValueError('Navigation approval expired.')
            return pin
        finally:
            if self._pending_navigation is pending:
                self._pending_navigation = None
                self._navigation_waiting.clear()

    def _document_guard(self):
        pending = self._pending_navigation
        if pending is not None and not self._blocked:
            return {'stage': 'device_approval', 'revision': pending['revision'],
                'fields': [], 'actions': [], 'groups': [], 'confirmable': False}
        return super()._document_guard()

    def _before_private_dispatch(self, state, action):
        self._document_post = None
        observed = next((item for item in state.get('scene', {}).get('actions', [])
                         if item['ref'] == action), None)
        if observed is not None and observed.get('dispatch') == 'submit':
            # One original same-origin native POST, never a redirect replay.
            self._document_post = (self.document_origin, time.monotonic() + 5)

    def _private_dispatch_rejected(self):
        self._document_post = None

    async def approve_origin(self, revision):
        async with self._lock:
            self._check_open()
            pending = self._pending_navigation
            current = self._agent_ui_current
            if (self._blocked or pending is None or pending['consumed'] or current is None
                    or current[0] != revision or current[1] != pending['revision']
                    or pending['future'].done()):
                raise RecoverableFormError('stale_form')
            pending['consumed'] = True
            try:
                from .website_navigation_transport import resolve_public_pin
                pin = await resolve_public_pin(urlsplit(pending['origin']).hostname, self._resolver)
                self._check_open()
                self._navigation_guard()
                if (self._navigation_closed or self._blocked or self._pending_navigation is not pending
                        or pending['future'].done()):
                    raise RecoverableFormError('stale_form')
                self._approved_origins[pending['origin']] = pin
                pending['future'].set_result(pin)
            except BaseException:
                if not pending['future'].done():
                    pending['future'].cancel()
                raise

    def agent_progress(self):
        return {'event_seq': self._agent_event_seq, 'outcome': self._agent_outcome}

    def _event(self, outcome):
        if self._agent_event_seq >= 1_000_000:
            raise ValueError('Agent website event limit reached.')
        self._agent_event_seq += 1
        self._agent_outcome = outcome

    def _clear_plan(self):
        self._composition_revision = self._composition_plan = self._composition_observation = None
        self._agent_ui_current = None

    def _check_open(self):
        if self._agent_closed:
            raise ValueError('Agent website session is closed.')

    def _scene(self, state):
        if 'scene' in state:
            return self._group_observation(state)
        waiting = self._pending_navigation is not None and state['stage'] == 'device_approval' and not self._blocked
        return validate_scene({'provider': 'website', 'destination': self.destination,
            'revision': state['revision'] if waiting else 'unavailable:0',
            'stage': 'wait' if waiting else 'unsupported_layout', 'controls': [],
            'actions': [], 'texts': [], 'after_input': self._human_values_entered,
            'overflow': {'controls': 0, 'actions': 0, 'texts': 0}})

    async def _observe(self):
        # A paused document has no load event to await. The trusted consent
        # state must be readable while its transport callback waits for input.
        if self._pending_navigation is not None:
            state = self._document_guard()
        elif self._navigation is None:
            state = await super()._observe()
        else:
            # Consent may become pending after this read begins, for example
            # while a click's navigation is replacing the previous document.
            reading = asyncio.create_task(super()._observe())
            waiting = asyncio.create_task(self._navigation_waiting.wait())
            try:
                await asyncio.wait((reading, waiting), return_when=asyncio.FIRST_COMPLETED)
                if self._pending_navigation is not None:
                    state = self._document_guard()
                else:
                    state = await reading
            finally:
                for task in (reading, waiting):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(reading, waiting, return_exceptions=True)
        # Missing inputs are not completion evidence. A method chooser and a
        # temporarily empty DOM both need another observation/composition step.
        # Automatic generic review never authorizes owner completion.
        if state['stage'] == 'review':
            scene = self._scene(state)
            state = dict(state, stage='form' if scene['stage'] == 'form' and scene['actions']
                         else 'agent_working', fields=[], actions=[])
        elif (state['stage'] == 'unsupported_layout' and self._human_values_entered
                and not self._scene(state)['controls'] and not self._scene(state)['actions']):
            state = dict(state, stage='agent_working', fields=[], actions=[])
        return dict(state, confirmable=False)

    def _refresh_plan(self, state):
        if self._composition_plan is not None and (state['stage'] != 'form'
                or state['revision'] != self._composition_revision
                or self._scene(state) != self._composition_observation):
            self._clear_plan()
            self._event('stale')

    def _observation(self, state):
        self._refresh_plan(state)
        scene = self._scene(state)
        stage = state['stage']
        if stage == 'form' and self._composition_plan is None:
            stage = 'agent_working'
        return validate_agent_observation({'revision': scene['revision'], 'stage': stage,
            'scene': model_context(scene), 'progress': self.agent_progress()})

    async def agent_observe(self):
        async with self._lock:
            self._check_open()
            return self._observation(await self._observe())

    async def _observe_after_action(self, previous):
        """Read through delayed hydration once; never repeat the accepted action."""
        observed = self._observation(await self._observe())

        def check_open():
            self._check_open()
            # A stale private submission can close disclosure before acquiring
            # the browser lock. Do not return a cached pre-input observation.
            if self._human_values_entered:
                raise RecoverableFormError('stale_form')

        def pending():
            if observed['stage'] not in {'agent_working', 'unsupported_layout'}:
                return False
            return (all(observed[key] == previous[key] for key in ('stage', 'scene', 'progress'))
                    or not observed['scene']['controls'] and not observed['scene']['actions'])

        check_open()
        if pending():
            try:
                async with asyncio.timeout(_POST_ACTION_SECONDS):
                    while pending():
                        await asyncio.sleep(_POST_ACTION_POLL_SECONDS)
                        check_open()
                        observed = self._observation(await self._observe())
                        check_open()
            except TimeoutError:
                # The latest complete read remains a proposal source only;
                # any later dispatch still checks its native revision afresh.
                check_open()
        return observed

    async def agent_present(self, revision, plan):
        async with self._lock:
            self._check_open()
            state = await self._observe()
            self._refresh_plan(state)
            if state['stage'] != 'form' or state['revision'] != revision:
                raise RecoverableFormError('stale_form')
            scene = self._scene(state)
            try:
                if scene.get('surfaces') and (type(plan) is not dict or 'surface_ref' not in plan):
                    raise ValueError
                compile_plan(plan, scene)
            except ValueError:
                raise AgentProposalError('invalid_plan') from None
            if self._composition_plan is not None and self._composition_plan != plan:
                raise AgentProposalError('human_step_pending')
            if self._composition_plan is None:
                self._composition_revision = revision
                self._composition_plan = deepcopy(plan)
                self._composition_observation = scene
            return self._observation(state)

    async def agent_act(self, revision, action_ref):
        async with self._lock:
            self._check_open()
            if self._composition_plan is not None:
                raise AgentProposalError('human_step_pending')
            if self._human_values_entered or self._agent_act_uncertain:
                raise AgentProposalError('navigation_requires_owner')
            state = await self._observe()
            if state['stage'] != 'form' or state['revision'] != revision:
                raise RecoverableFormError('stale_form')
            scene = self._scene(state)
            action = next((item for item in scene['actions'] if item['ref'] == action_ref), None)
            if action is None:
                raise AgentProposalError('invalid_plan')
            if (not action['enabled'] or not action['permitted']
                    or action['kind'] != 'link' and action['form'] is not None
                    and any(field['form'] == action['form'] for field in scene['controls'])):
                raise AgentProposalError('navigation_requires_owner')
            plan = {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                'revision': revision, 'anchor': action_ref, 'fields': [],
                'actions': [{'ref': action_ref, 'presentation': 'primary'}], 'message_ref': None}
            try:
                compile_plan(plan, scene)
            except ValueError:
                raise AgentProposalError('invalid_plan') from None
            previous = self._observation(state)
            try:
                result = await self.page.evaluate(_AGENT_ACTION_SCRIPT, {
                    'stateKey': self._state_key, 'expectedOrigin': self.document_origin,
                    'allowOriginApproval': self._navigation is not None,
                    'maxSelectOptions': MAX_SELECT_OPTIONS,
                    'operation': 'submit', 'revision': revision, 'action': action_ref, 'values': {},
                    'plan': plan, 'mayConfirm': False, 'afterInput': False,
                    'redactions': self._redactions, 'textRedactions': self._text_redactions,
                    'credentialValues': self._credential_values})
            except asyncio.CancelledError:
                self._agent_act_uncertain = True
                raise
            except Exception:
                self._agent_act_uncertain = True
                raise SubmitUncertainError() from None
            if result == {'accepted': False, 'code': 'stale_form'}:
                raise RecoverableFormError('stale_form')
            if result == {'accepted': False, 'code': 'navigation_requires_owner'}:
                raise AgentProposalError('navigation_requires_owner')
            if result != {'accepted': True}:
                self._agent_act_uncertain = True
                raise SubmitUncertainError()
            # No human progress/history or completion evidence is fabricated.
            try:
                return await self._observe_after_action(previous)
            except asyncio.CancelledError:
                self._agent_act_uncertain = True
                raise

    def _private_snapshot(self, state):
        self._refresh_plan(state)
        if state['stage'] == 'form':
            if self._composition_plan is None:
                state = dict(state, stage='agent_working', fields=[], actions=[], confirmable=False)
            else:
                state = self._selected_composition(state)
        form = {'provider': self.provider, 'destination': self.destination,
            'current_origin': self.document_origin, 'form_schema': self.form_schema,
            'stage': state['stage'], 'fields': state['fields'],
            'can_complete': (self._agent_human_completed and not self._agent_human_uncertain
                             and not self._agent_act_uncertain
                             and self._pending_navigation is None
                             and self.document_origin == self.destination
                             and state['stage'] not in {'provider_blocked', 'unsupported_challenge'}),
            'actions': [dict(action, field_refs=[field['name'] for field in state['fields']], available=True)
                if 'field_refs' not in action else action for action in state['actions']],
            'message': ('Агент аналізує поточну сторінку.' if state['stage'] == 'agent_working'
                        else self._state_message(state))}
        if self._pending_navigation is not None and state['stage'] == 'device_approval' and not self._blocked:
            form['origin_approval'] = {'origin': self._pending_navigation['origin']}
        # Native DOM revisions authorize agent proposals. Private presentation
        # revisions also bind the selected plan, so working -> form cannot alter
        # the renderer's shape under one revision. Stable polls retain drafts.
        document = state['revision'].rsplit(':', 1)[0]
        if document != self._agent_ui_document:
            self._agent_ui_document = document
            self._agent_ui_prefix = str(uuid.uuid4())
        signature = (state['revision'], deepcopy(self._composition_plan), deepcopy(form))
        if signature != self._agent_ui_signature or self._agent_ui_current is None:
            self._agent_ui_sequence += 1
            self._agent_ui_signature = signature
            self._agent_ui_current = (self._agent_ui_prefix + ':' + str(self._agent_ui_sequence),
                                      state['revision'], self._composition_plan)
        return dict(form, revision=self._agent_ui_current[0])

    async def snapshot(self):
        async with self._lock:
            self._check_open()
            return self._private_snapshot(await self._observe())

    async def verified(self):
        async with self._lock:
            self._check_open()
            return (await self._observe())['stage'] == 'authenticated'

    async def submit(self, revision, action, values):
        self._check_open()
        target = self._composition_plan
        current = self._agent_ui_current
        if (current is None or current[0] != revision or target is None
                or current[2] is not target or current[1] != target['revision']):
            self._close_text_disclosure(values)
            raise RecoverableFormError('stale_form')
        native_revision = current[1]
        outcome = 'uncertain'
        try:
            await super().submit(native_revision, action, values)
            outcome = 'submitted'
            self._agent_human_completed = True
        except RecoverableFormError:
            outcome = 'stale'
            raise
        except ValueError as error:
            outcome = 'uncertain' if isinstance(error, SubmitUncertainError) else 'stale'
            raise
        finally:
            if outcome == 'uncertain':
                self._agent_human_uncertain = True
            # A delayed duplicate for an old form cannot consume a newer plan.
            if target is self._composition_plan and target['revision'] == native_revision:
                self._clear_plan()
                self._event(outcome)

    async def confirmable(self, revision):
        async with self._lock:
            self._check_open()
            return False

    async def owner_finishable(self, revision):
        """A human assertion bound to the current private UI, never login proof."""
        async with self._lock:
            self._check_open()
            current = self._private_snapshot(await self._observe())
            if current['revision'] != revision:
                raise RecoverableFormError('stale_form')
            return current['can_complete']

    async def retain_session(self, seconds):
        """Trusted broker promotion after root-scoped content checks succeed."""
        self._check_open()
        self._navigation_guard()
        if self._retained or self._pending_navigation is not None or self.document_origin != self.destination:
            raise ValueError('Private navigation cannot be retained.')
        if self._navigation is not None:
            self._navigation.retain_session(seconds, {urlsplit(self.destination).hostname})
        self._approved_origins = {self.destination: self._approved_origins[self.destination]}
        self._document_post = None
        self._retained = True

    def diagnostics(self):
        return {**super().diagnostics(), 'agent_mode': True, 'agent_progress': self.agent_progress(),
                'navigation': self._navigation.diagnostics() if self._navigation is not None else None}

    async def close(self):
        self._agent_closed = True
        self._navigation_closed = True
        self._clear_plan()
        pending, self._pending_navigation = self._pending_navigation, None
        if pending is not None and not pending['future'].done():
            pending['future'].cancel()
        self._document_post = None
        self._approved_origins.clear()
        task = self._initial_document_task
        if task is not None and not task.done():
            task.cancel()
        try:
            if self._navigation is not None:
                await self._navigation.close()
        finally:
            try:
                await super().close()
            finally:
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
