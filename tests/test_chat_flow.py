import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chat_flow
import tradepilot as tp


class ChatFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.db = tp.connect(str(self.folder / 'chat.db'))
        self.conversation = 'studio-user-session-A'

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def call(self, action='reply', value=None):
        return chat_flow.step(self.db, self.conversation, {'action': action, 'value': value}, self.folder / 'outputs')

    def profile(self):
        self.call('start')
        for value in ('Factory', 'Industrial belt', 'Conveying', 'Germany', 'Machinery', 'Procurement'):
            result = self.call(value=value)
        self.assertEqual(result['stage'], 'icp_approval')
        self.call(value='确认')

    def test_full_conversation_with_evidence_and_report(self):
        self.profile()
        self.assertEqual(self.call('status')['stage'], 'candidate_choice')
        candidate = {'name': 'Example GmbH', 'website': 'https://example.org',
                     'source_url': 'https://example.org/about', 'country': 'Germany', 'industry': 'Machinery'}
        result = self.call(value=candidate)
        self.assertEqual(result['stage'], 'candidate_import')
        self.call(value='完成')
        self.assertEqual(self.call('status')['stage'], 'evidence_choice')
        self.call(value={'claim': 'Conveyor manufacturing', 'excerpt': 'We make conveyors',
                         'source_url': 'https://example.org/products', 'source_type': 'official_website'})
        self.call(value='确认核验')
        result = self.call(value='评估')
        self.assertEqual(result['stage'], 'qualification_review')
        self.call(value='通过')
        self.call(value='完成')
        result = self.call(value='跳过')
        self.assertTrue(result['done'])
        folder = Path(result['output_dir'])
        for name in ('report.md', 'result.json', 'prospects.csv'):
            self.assertTrue((folder / name).exists())
        self.assertIn('Conveyor manufacturing', (folder / 'report.md').read_text(encoding='utf-8'))
        self.assertEqual(tp.stats(self.db)['qualified'], 1)
        self.assertEqual(tp.stats(self.db)['sent'], 0)
        self.assertEqual(self.call('status')['stage'], 'done')

    def test_pause_is_stable_and_restarts_with_new_run(self):
        self.profile()
        before = self.call('status')
        self.assertEqual(self.call(value='暂停')['stage'], before['stage'])
        self.assertEqual(self.call('start')['run_id'], before['run_id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM icp_profiles').fetchone()[0], 1)

    def test_invalid_input_does_not_advance(self):
        self.call('start')
        with self.assertRaises(ValueError):
            self.call(value='')
        self.assertEqual(self.call('status')['stage'], 'supplier')
        self.profile()
        with self.assertRaises(ValueError):
            self.call(value='完成')
        self.assertEqual(self.call('status')['stage'], 'candidate_choice')
        self.call(value='准备录入')
        with self.assertRaises(ValueError):
            self.call(value='完成')
        self.assertEqual(self.call('status')['stage'], 'candidate_import')

    def test_unverified_prospect_is_not_qualified(self):
        self.profile()
        self.call(value={'name': 'Example', 'website': 'https://example.org',
                         'source_url': 'https://example.org/about', 'country': 'Germany', 'industry': 'Machinery'})
        self.call(value='完成')
        result = self.call(value='评估')
        self.assertEqual(result['stage'], 'next_company')
        self.call(value='完成')
        self.assertEqual(self.call('status')['stage'], 'draft_choice')
        self.assertEqual(self.call(value='草稿')['stage'], 'draft_choice')
        result = self.call(value='跳过')
        self.assertTrue(result['done'])
        self.assertEqual(tp.stats(self.db)['qualified'], 0)

    def test_conversation_isolation(self):
        self.call('start')
        self.call(value='Factory A')
        self.conversation = 'studio-user-session-B'
        self.assertEqual(self.call('start')['stage'], 'supplier')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_runs').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
