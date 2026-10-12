"""One bounded delegated browser task; callbacks retain all browser authority.

Only sanitized observations reach the injected planner. Inference runs without
a browser lock; act/present callbacks must revalidate the native revision under
their own lease lock. This task never submits private values or establishes auth.
"""

import asyncio
from copy import deepcopy
import hashlib
import json

from .signin_providers import RecoverableFormError, SubmitUncertainError
from .website_agent import validate_agent_observation
from .website_planner import RESPONSE_MODEL, WebsitePlannerError, _number


TASK_ERRORS = frozenset({
    'unavailable', 'invalid_response', 'invalid_plan', 'no_candidate',
    'invalid_observation', 'invalid_proposal', 'unsupported', 'step_limit',
    'call_limit', 'callback_failed', 'progress_regressed', 'action_uncertain',
    'navigation_requires_owner', 'human_step_pending', 'composition_unavailable',
    'readiness_timeout',
})
TASK_STATES = frozenset({'idle', 'running', 'waiting_for_owner', 'requires_agent', 'cancelled'})
TASK_PHASES = frozenset({'idle', 'observing', 'planning', 'acting', 'presenting', 'waiting', 'stopped'})


def validate_task_snapshot(value):
    """Copy the exact safe status contract; never forward callback properties."""
    try:
        if (type(value) is not dict or set(value) != {
                'state', 'phase', 'steps', 'calls', 'progress', 'error'}
                or type(value['state']) is not str or value['state'] not in TASK_STATES
                or type(value['phase']) is not str or value['phase'] not in TASK_PHASES
                or type(value['steps']) is not int or not 0 <= value['steps'] <= 18
                or type(value['calls']) is not int or not 0 <= value['calls'] <= 12
                or value['error'] is not None and (
                    type(value['error']) is not str or value['error'] not in TASK_ERRORS)):
            raise ValueError
        progress = value['progress']
        if (type(progress) is not dict or set(progress) != {'event_seq', 'outcome'}
                or type(progress['event_seq']) is not int or not 0 <= progress['event_seq'] <= 1000000
                or type(progress['outcome']) is not str
                or progress['outcome'] not in {'idle', 'submitted', 'stale', 'uncertain'}):
            raise ValueError
        return deepcopy(value)
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid delegated website task status.') from None


class WebsiteTask:
    """A lease-local loop, stopped by its owner on expiry, completion or restart."""

    def __init__(self, planner, *, observe, act, present, poll_seconds=0.5,
                 max_steps=18, max_calls=12, readiness_seconds=10):
        if (type(max_steps) is not int or not 1 <= max_steps <= 18
                or type(max_calls) is not int or not 1 <= max_calls <= 12
                or type(poll_seconds) not in (int, float) or not 0 < poll_seconds <= 5
                or type(readiness_seconds) not in (int, float) or not 0 < readiness_seconds <= 300
                or not all(callable(callback) for callback in (observe, act, present))):
            raise ValueError('Invalid delegated website task configuration.')
        self.planner = planner
        self._observe, self._act, self._present = observe, act, present
        self.poll_seconds, self.max_steps, self.max_calls = poll_seconds, max_steps, max_calls
        self.readiness_seconds = readiness_seconds
        self._status = {'state': 'idle', 'phase': 'idle', 'steps': 0, 'calls': 0,
                        'progress': {'event_seq': 0, 'outcome': 'idle'}, 'error': None}
        self._task = None
        self._wake = asyncio.Event()
        self._closed = False
        self._seen, self._dispatched = set(), set()
        self._limit_error = None
        self._requests = []
        self._readiness_key = self._readiness_deadline = None

    def snapshot(self):
        return validate_task_snapshot(self._status)

    def diagnostics(self):
        """Trusted private diagnostics only; deliberately outside MCP status."""
        costs = [item['cost'] for item in self._requests if item['cost'] is not None]
        return {'requests': deepcopy(self._requests), 'known_cost': sum(costs),
                'unknown_cost_calls': self._status['calls'] - len(costs),
                'elapsed_ms': sum(item['elapsed_ms'] for item in self._requests)}

    def start(self):
        if self._task is None and not self._closed:
            self._status.update(state='running', phase='observing')
            self._task = asyncio.create_task(self._run())
        return self.snapshot()

    def wake(self):
        self._wake.set()

    async def close(self):
        self._closed = True
        self._status.update(state='cancelled', phase='stopped', error=None)
        self.wake()
        # A broker callback may finish the lease from within this task.
        # Awaiting or cancelling ourselves would deadlock or interrupt cleanup.
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _stop(self, error):
        self._status.update(state='requires_agent', phase='stopped',
                            error=error if error in TASK_ERRORS else 'callback_failed')

    async def _reserve(self):
        if self._closed or self._status['calls'] >= self.max_calls:
            self._limit_error = 'call_limit'
            raise WebsitePlannerError('unavailable')
        self._status['calls'] += 1

    def _record(self, value):
        if (type(value) is not dict or set(value) != {
                'phase', 'status', 'model', 'cost', 'elapsed_ms', 'error'}
                or type(value['phase']) is not str or value['phase'] not in {'anchor', 'surface', 'components'}
                or type(value['status']) is not str or value['status'] not in {'selected', 'abstained', 'failed'}
                or value['model'] is not None and value['model'] != RESPONSE_MODEL
                or value['cost'] is not None and (not _number(value['cost']) or not 0 <= value['cost'] <= 1000000)
                or type(value['elapsed_ms']) is not int or not 0 <= value['elapsed_ms'] <= 2147483647
                or value['error'] is not None and (type(value['error']) is not str or value['error'] not in {
                    'unavailable', 'invalid_response', 'invalid_plan', 'no_candidate'})
                or len(self._requests) >= self._status['calls']):
            raise WebsitePlannerError('invalid_response')
        self._requests.append(deepcopy(value))

    async def _pause(self):
        try:
            await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    def _checked(self, value):
        try:
            observed = validate_agent_observation(value)
        except ValueError:
            self._stop('invalid_observation')
            return None
        if observed['progress']['event_seq'] < self._status['progress']['event_seq']:
            self._stop('progress_regressed')
            return None
        self._status['progress'] = dict(observed['progress'])
        if observed['progress']['outcome'] == 'uncertain':
            self._stop('action_uncertain')
            return None
        return observed

    async def _step(self, observed):
        if observed['stage'] in {'form', 'device_approval', 'authenticated'}:
            self._readiness_key = self._readiness_deadline = None
            self._status.update(state='waiting_for_owner', phase='waiting')
            return
        if observed['stage'] in {'provider_blocked', 'unsupported_challenge'}:
            self._stop('unsupported')
            return
        # Revision and human event are authority boundaries. No repeated costs
        # or actions on an unchanged observation, including a model wait result.
        key = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).digest()
        empty = not observed['scene']['controls'] and not observed['scene']['actions']
        readiness_key = (('empty', observed['progress']['event_seq'], observed['progress']['outcome'])
                         if empty else key)
        now = asyncio.get_running_loop().time()
        if self._readiness_key != readiness_key:
            self._readiness_key = readiness_key
            self._readiness_deadline = now + self.readiness_seconds
        elif now >= self._readiness_deadline:
            # A generic review or empty page is unresolved, not completion.
            # Return the same workspace without another model call/action.
            self._stop('readiness_timeout')
            return
        if empty:
            self._status.update(state='running', phase='waiting')
            return
        if key in self._seen:
            self._status.update(phase='waiting')
            return
        if self._status['steps'] >= self.max_steps:
            self._stop('step_limit')
            return
        self._seen.add(key)
        self._status['steps'] += 1
        self._status.update(state='running', phase='planning')
        proposal = await self.planner.plan(observed, reserve=self._reserve, record=self._record)
        if self._closed:
            return
        if (type(proposal) is not dict or proposal.get('revision') != observed['revision']
                or proposal.get('operation') not in {'act', 'present', 'wait', 'unsupported'}):
            self._stop('invalid_proposal')
            return
        operation, ref = proposal['operation'], proposal.get('action_ref')
        if operation in {'wait', 'unsupported'}:
            if ref is not None or proposal.get('plan') is not None:
                self._stop('invalid_proposal')
            elif operation == 'unsupported':
                # Trusted blocked stages exit above, before inference. Distinguish
                # a planner abstention from that browser/policy condition.
                self._stop('no_candidate')
            else:
                self._status.update(state='waiting_for_owner', phase='waiting')
            return
        if (type(ref) is not str or not any(action['ref'] == ref and (operation == 'present' or action['permitted'])
                for action in observed['scene']['actions'])):
            self._stop('invalid_proposal')
            return
        if operation == 'act':
            dispatch = (observed['revision'], ref)
            if (observed['scene']['after_input'] or proposal.get('plan') is not None
                    or dispatch in self._dispatched):
                self._stop('invalid_proposal')
                return
            # Reserve before dispatch. Stale/uncertain results cannot replay it.
            self._dispatched.add(dispatch)
            self._status['phase'] = 'acting'
            value = await self._act(observed['revision'], ref)
        else:
            if type(proposal.get('plan')) is not dict:
                self._stop('invalid_proposal')
                return
            self._status['phase'] = 'presenting'
            value = await self._present(observed['revision'], deepcopy(proposal['plan']))
        if not self._closed:
            self._checked(value)
            if self._status['state'] != 'requires_agent':
                self._status.update(state='waiting_for_owner' if operation == 'present' else 'running',
                                    phase='waiting')

    async def _run(self):
        try:
            while not self._closed and self._status['state'] != 'requires_agent':
                try:
                    self._status['phase'] = 'observing'
                    value = await self._observe()
                    if self._closed:
                        break
                    observed = self._checked(value)
                    if observed is not None:
                        await self._step(observed)
                except SubmitUncertainError:
                    self._stop('action_uncertain')
                except RecoverableFormError as error:
                    if error.code in {'stale_form', 'navigation_pending'}:
                        self._status.update(state='running', phase='waiting')
                    elif error.code == 'human_step_pending':
                        self._status.update(state='waiting_for_owner', phase='waiting')
                    else:
                        self._stop(error.code)
                except WebsitePlannerError as error:
                    self._stop(self._limit_error or error.code)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._stop('callback_failed')
                if not self._closed and self._status['state'] != 'requires_agent':
                    await self._pause()
        except asyncio.CancelledError:
            if not self._closed:
                self._status.update(state='cancelled', phase='stopped', error=None)
            raise
