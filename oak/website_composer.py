"""Explicit private Jev configuration and a bounded composer per browser lease."""

import os
import asyncio
from pathlib import Path
import re
import stat

from .website_planner import OpenRouterWebsitePlanner, WebsitePlannerError
from .website_scene_planner import ScenePlanner
from copy import deepcopy


def validate_config(value):
    if (type(value) is not dict or set(value) != {'backend', 'model', 'key_file', 'max_calls'}
            or value['backend'] != 'jev' or value['model'] != 'typesafe/jev-1.13'
            or type(value['max_calls']) is not int or not 1 <= value['max_calls'] <= 12
            or type(value['key_file']) is not str
            or not re.fullmatch(r'/[A-Za-z0-9_./-]{1,240}', value['key_file'])
            or '..' in Path(value['key_file']).parts):
        raise ValueError('Invalid explicit website composer configuration.')
    return dict(value)


def read_key(path, broker_uid):
    """Read a raw key, never an env file; do not follow the credential symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, broker_uid}
                    or info.st_mode & 0o077 or not 1 <= info.st_size <= 4096):
                raise ValueError
            key = stream.read(4097).decode('ascii').strip()
        if not 20 <= len(key) <= 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in key):
            raise ValueError
        return key
    except Exception:
        raise ValueError('A private broker-readable API key file is required.') from None


class LeaseComposer:
    def __init__(self, planner, max_calls):
        self.planner = planner
        self.max_calls = max_calls
        self.calls = 0
        self.cost_usd = 0.0
        self.scene_capable = getattr(planner, 'scene_capable', False) is True
        self.events = []
        self.unknown_cost_calls = 0
        self.plan_status = 'idle'
        self.last_error = None

    def _reserve(self):
        if self.calls >= self.max_calls:
            raise WebsitePlannerError('unavailable')
        self.calls += 1

    def _record(self, metadata):
        # Only transport-owned enum/count/cost metadata; no page descriptions,
        # input, model payloads, URLs or credentials enter this lease trace.
        self.events.append(deepcopy(metadata))
        if metadata['cost'] is None:
            self.unknown_cost_calls += 1
        else:
            self.cost_usd += metadata['cost']

    def diagnostics(self):
        return {'api_calls': self.calls, 'max_calls': self.max_calls,
                'known_cost_usd': self.cost_usd, 'unknown_cost_calls': self.unknown_cost_calls,
                'events': deepcopy(self.events), 'plan_status': self.plan_status,
                'last_error': self.last_error}

    async def plan_for_anchor(self, observation, anchor_ref):
        if not self.scene_capable or type(anchor_ref) is not str:
            raise WebsitePlannerError('invalid_plan')
        return await self.plan(observation, human_anchor=anchor_ref)

    async def plan(self, observation, *, human_anchor=None):
        if self.scene_capable:
            self.plan_status, self.last_error = 'planning', None
            try:
                result = await self.planner.plan(observation, reserve=self._reserve, record=self._record,
                    **({'human_anchor': human_anchor} if human_anchor is not None else {}))
            except asyncio.CancelledError:
                self.plan_status, self.last_error = 'cancelled', 'unavailable'
                raise
            except WebsitePlannerError as error:
                self.plan_status, self.last_error = ('abstained' if error.code == 'no_candidate' else 'failed'), error.code
                raise
            except Exception:
                self.plan_status, self.last_error = 'failed', 'unavailable'
                raise WebsitePlannerError('unavailable') from None
            self.plan_status = 'composed'
            return result
        if self.calls >= self.max_calls:
            raise WebsitePlannerError('unavailable')
        # Failed/uncertain requests consume the quota too. Polling must never
        # retry them; WebsiteForms caches both success and failure by revision.
        self.calls += 1
        result = await self.planner.plan(observation)
        usage = result.get('usage', {})
        self.cost_usd += usage.get('cost', 0.0)
        return result


def composer_factory(value, broker_uid):
    config = validate_config(value)
    planner = ScenePlanner(read_key(config['key_file'], broker_uid), config['model'])
    return lambda: LeaseComposer(planner, config['max_calls'])


def step_planner_factory(value, broker_uid):
    """An independent request budget and planner instance for each delegated lease."""
    from .website_step_planner import StepPlanner
    from .website_goal import DEFAULT_GOAL
    config = validate_config(value)
    key = read_key(config['key_file'], broker_uid)
    return lambda goal=DEFAULT_GOAL: StepPlanner(key, config['model'], max_calls=config['max_calls'], goal=goal)
