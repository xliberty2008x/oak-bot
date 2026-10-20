"""Owner conversation controls affect only the scoped improvement loop."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.sessions import SessionStore
from oak.telegram import TelegramGateway
from oak.commands import command_names, help_text, menu_commands


class FixtureController:
    def __init__(self, db):
        self.sessions = SessionStore(db)
        self.controls = []
        self.submit = AsyncMock()
        self.stop = AsyncMock()
        self.enabled = True
        self.failed = False
        self.paused = False
        self.decision = {'accepted': False, 'reasons': ['PRIVATE_REASON_CANARY']}

    def improvement_control(self, chat_id, action='status'):
        self.controls.append((chat_id, action))
        if self.failed:
            raise RuntimeError('PRIVATE_ERROR_CANARY')
        if not self.enabled:
            return {'enabled': False}
        if action in {'stop', 'resume'}:
            self.paused = action == 'stop'
        return {'enabled': True, 'phase': 'running', 'paused': self.paused,
            'cases': 4, 'approved_cases': 1, 'awaiting_adapter': 2, 'awaiting_approval': 1,
            'active_version': 'a' * 64, 'last_cycle': 1,
            'last_decision': self.decision,
            'unexpected_private_field': 'PRIVATE_VALUE_CANARY'}


class ImprovementControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.controller = FixtureController(db)
        self.gateway = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temporary.name))
        self.addCleanup(self.gateway.db.close)
        self.gateway._notice = AsyncMock()

    async def command(self, text, *, sender=7, topic=None, update_id=1):
        message = {'chat': {'id': 7, 'type': 'private'}, 'from': {'id': sender}, 'text': text}
        if topic is not None:
            message['message_thread_id'] = topic
        await self.gateway.handle_update({'update_id': update_id, 'message': message})
        return self.gateway._notice.await_args.args[1] if self.gateway._notice.await_count else None

    async def test_default_status_exposes_counts_and_decision_without_private_values(self):
        response = await self.command('/improve')
        self.assertEqual(self.controller.controls, [(7, 'status')])
        self.assertIn('Самопокращення: працює.', response)
        self.assertIn('Випадки: 4', response)
        self.assertIn('Без готового сценарію: 2', response)
        self.assertIn('очікують дозволу: 1', response)
        self.assertIn('відхилено', response)
        self.assertIn('aaaaaaaaaaaa', response)
        self.assertNotIn('PRIVATE_', response)
        self.controller.submit.assert_not_awaited()

    async def test_improvement_control_is_discoverable_in_the_shared_owner_inventory(self):
        self.assertIn('/improve', command_names())
        self.assertIn('/improve status|stop|resume — Самопокращення', help_text())
        self.assertIn({'command': 'improve', 'description': 'Самопокращення'}, menu_commands())

    async def test_unmeasured_closures_are_never_reported_as_quality_rejections(self):
        for ordinal,classification in enumerate(('infrastructure_abort','search_no_candidate')):
            with self.subTest(classification=classification):
                self.controller.decision = {'accepted':False,'reasons':['PRIVATE_REASON_CANARY'],
                    'outcome':'not_admitted','classification':classification,
                    'evaluated':False,'quality_rejection':False}
                response = await self.command('/improve status',update_id=ordinal+1)
                self.assertIn('якість не оцінювали',response)
                self.assertNotIn('відхилено',response)
                self.assertNotIn('Останній кандидат:',response)
                self.assertNotIn('PRIVATE_',response)
                self.assertIn('технічний збій' if ordinal == 0 else 'кандидат не сформований',response)
        self.controller.submit.assert_not_awaited()

    async def test_measured_admission_and_rejection_keep_their_verdicts(self):
        for ordinal,accepted in enumerate((False,True)):
            with self.subTest(accepted=accepted):
                self.controller.decision = {'accepted':accepted,'reasons':[],
                    'evaluated':True,'quality_rejection':not accepted}
                response = await self.command('/improve status',update_id=ordinal+1)
                self.assertIn('Останній кандидат: '+('прийнято.' if accepted else 'відхилено.'),response)
                self.assertNotIn('якість не оцінювали',response)

    async def test_stop_and_resume_change_only_loop_in_exact_topic(self):
        response = await self.command('/improve stop', topic=9)
        scope = self.controller.sessions.resolve(7, 9)
        self.assertEqual(self.controller.controls, [(scope, 'stop')])
        self.assertIn('призупинено', response)
        self.assertTrue(self.controller.paused)
        response = await self.command('/improve resume', topic=9, update_id=2)
        self.assertEqual(self.controller.controls[-1], (scope, 'resume'))
        self.assertFalse(self.controller.paused)
        self.assertIn('наявного бюджету', response)
        self.controller.stop.assert_not_awaited()
        self.controller.submit.assert_not_awaited()

    async def test_unauthorized_sender_cannot_read_or_control_loop(self):
        await self.command('/improve stop', sender=8)
        self.assertEqual(self.controller.controls, [])
        self.gateway._notice.assert_not_awaited()

    async def test_unconfigured_chat_is_reported_without_starting_a_model(self):
        self.controller.enabled = False
        response = await self.command('/improve status')
        self.assertIn('не увімкнено для цієї розмови', response)
        self.controller.submit.assert_not_awaited()

    async def test_control_rejects_other_actions_and_does_not_disclose_internal_errors(self):
        response = await self.command('/improve approve')
        self.assertIn('/improve status', response)
        self.assertEqual(self.controller.controls, [])
        self.controller.failed = True
        response = await self.command('/improve status', update_id=2)
        self.assertIn('зараз недоступний', response)
        self.assertNotIn('PRIVATE_ERROR_CANARY', response)


if __name__ == '__main__':
    unittest.main()
