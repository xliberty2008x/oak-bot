import sqlite3
import tempfile
import unittest
from pathlib import Path

from oak.memory import MemoryStore


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.memory = MemoryStore(self.db)

    def save(self, chat_id=7, **changes):
        values = {'key': 'character-animation', 'title': 'Character animation',
                  'when_to_use': 'Local animation with blinking eyes and moving hair',
                  'procedure': 'Inspect closing, fully closed, and reopening eyes at native size.',
                  'limitations': 'One observed case; inspect the new source before reusing regions.'}
        values.update(changes)
        return self.memory.save_experience(chat_id, **values)

    def test_experience_is_separate_from_preferences_and_scoped_to_conversation(self):
        self.memory.remember(7, 'Owner likes animation.')
        first = self.save()
        other = self.save(8, procedure='An unrelated conversation procedure.')
        self.assertNotEqual(first['id'], other['id'])
        self.assertEqual(len(self.memory.search(7, 'animation')), 1)
        notes = self.memory.experience_search(7, 'animation')
        self.assertEqual([note['id'] for note in notes], [first['id']])
        self.assertNotIn('unrelated', notes[0]['procedure'])
        self.assertEqual(self.memory.experience_search(9), [])
        self.assertFalse(self.memory.forget_experience(8, first['id']))
        self.assertTrue(self.memory.forget_experience(7, first['id']))
        self.assertEqual(self.memory.experience_search(7, 'animation'), [])
        self.assertEqual(len(self.memory.experience_search(8, 'animation')), 1)
        self.assertEqual(len(self.memory.search(7, 'animation')), 1)

    def test_upsert_updates_one_lesson_and_replaces_fts_content(self):
        first = self.save(key='  ANIMATION   recipe ', procedure='Prior failed zebra technique.')
        created = self.memory.experience_search(7)[0]['created']
        changed = self.save(key='animation recipe', procedure='Revised otter technique.')
        self.assertEqual(first['id'], changed['id'])
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM experience_notes').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM experience_search').fetchone()[0], 1)
        self.assertEqual(self.memory.experience_search(7, 'zebra'), [])
        result = self.memory.experience_search(7, 'otter')[0]
        self.assertEqual(result['id'], first['id'])
        self.assertEqual(result['created'], created)
        self.assertGreaterEqual(result['updated'], created)

    def test_modified_procedure_requires_new_verification(self):
        evidence = [{'review_id': 'review-pass', 'artifact_sha256': 'a' * 64,
                     'outcome': 'passed', 'path': 'artifacts/animation.mp4'}]
        passed = self.save(evidence=evidence, verified=True)
        self.assertEqual(passed['status'], 'proven')
        self.assertEqual(self.memory.experience_search(7)[0]['evidence'], evidence)
        changed = self.save(procedure='A new method that has not been exercised.', evidence=evidence)
        self.assertEqual(changed['id'], passed['id'])
        self.assertEqual(changed['status'], 'unverified')
        self.assertEqual(self.memory.experience_search(7)[0]['status'], 'unverified')
        with self.assertRaises(ValueError):
            self.save(verified=True)
        self.assertEqual(self.memory.experience_search(7)[0]['revision'], 2)

    def test_validation_does_not_replace_existing_lesson(self):
        saved = self.save()
        invalid = [
            {'key': ''}, {'title': 7}, {'when_to_use': ''}, {'procedure': 'x' * 16001},
            {'limitations': 'x' * 4001}, {'evidence': {}}, {'verified': 'proven'},
            {'evidence': [{}]}, {'evidence': [{'unbounded_transcript': 'text'}]},
            {'evidence': [{'review_id': None}]}, {'evidence': [{'review_id': 'x' * 2001}]},
            {'evidence': [{'review_id': str(index)} for index in range(13)]},
        ]
        for changes in invalid:
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                self.save(**changes)
        result = self.memory.experience_search(7)[0]
        self.assertEqual(result['id'], saved['id'])
        self.assertEqual(result['revision'], 1)

    def test_fts_query_cannot_escape_scope_or_interpret_query_operators(self):
        self.save()
        self.save(8, procedure='Private password recovery.')
        self.assertEqual(self.memory.experience_search(7, '" OR password NOT animation:*')[0]['title'],
                         'Character animation')
        self.assertEqual(self.memory.experience_search(7, 'password'), [])
        with self.assertRaises(ValueError):
            self.memory.experience_search(7, ['animation'])
        with self.assertRaises(ValueError):
            self.memory.experience_search(7, 'x' * 8001)

    def test_reopen_preserves_evidence_and_adds_experience_without_changing_old_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite3'
            first_db = sqlite3.connect(path)
            first_store = MemoryStore(first_db)
            preference = first_store.remember(7, 'Existing private preference.')
            evidence = [{'review_id': 'durable-review', 'outcome': 'passed'}]
            lesson = first_store.save_experience(7, 'render', 'Render', 'Video requests',
                                                'Inspect actual frames.', evidence=evidence, verified=True)
            first_db.close()
            with sqlite3.connect(path) as second_db:
                reopened = MemoryStore(second_db)
                result = reopened.experience_search(7, 'video')[0]
                self.assertEqual(result['id'], lesson['id'])
                self.assertEqual(result['status'], 'proven')
                self.assertEqual(result['evidence'], evidence)
                self.assertEqual(reopened.search(7)[0]['id'], preference)
                self.assertEqual(reopened.experience_search(8), [])


if __name__ == '__main__':
    unittest.main()
