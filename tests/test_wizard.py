import builtins
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tradepilot as tp
import wizard


class GuidedRun(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = tp.connect(str(self.base / 'data.db'))
        (self.base / 'candidates.json').write_text(json.dumps([{
            'name': 'Example GmbH', 'website': 'https://example.org',
            'source_url': 'https://example.org/company', 'country': 'Germany',
            'industry': 'Machinery'}]), encoding='utf-8')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def answers(self, values):
        return patch.object(builtins, 'input', side_effect=values)

    def test_full_guided_delivery(self):
        values = ['无', 'Factory', 'Industrial belt', 'Conveying', 'Germany', 'Machinery', 'Procurement',
                  'y', 'Alice', 'n', 'y', '2', str(self.base / 'candidates.json'),
                  'n', 'y', 'Website shows conveyors', 'We make conveyors',
                  'https://example.org/products', 'official_website', 'n',
                  'y', 'Alice', 'y', 'Alice', 'n']
        with self.answers(values):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'done')
        folder = Path(session['state']['output_dir'])
        self.assertTrue((folder / 'result.json').exists())
        self.assertTrue((folder / 'prospects.csv').exists())
        report = (folder / 'report.md').read_text(encoding='utf-8')
        self.assertIn('https://example.org/products', report)
        self.assertIn('qualified', report)
        self.assertEqual(tp.stats(self.db)['sent'], 0)
        self.assertEqual(tp.stats(self.db)['qualified'], 1)
        self.assertEqual(wizard.resume(self.db, session['id'])['stage'], 'done')

    def test_pause_then_resume_without_double_icp(self):
        with self.answers(['无', 'Factory', 'Belt', 'Conveying', 'Germany', 'Machinery', 'Buyer', 'n', 'n']):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'approval')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM icp_profiles').fetchone()[0], 1)
        with self.answers(['y', 'Alice', 'n', 'n']):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'discovery')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM icp_profiles').fetchone()[0], 1)

    def test_missing_document_does_not_create_icp(self):
        with self.answers([str(self.base / 'missing.pdf')]):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'profile')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM icp_profiles').fetchone()[0], 0)

    def test_unverified_only_never_auto_qualifies(self):
        values = ['无', 'Factory', 'Belt', 'Conveying', 'Germany', 'Machinery', 'Buyer',
                  'y', 'Alice', 'n', 'y', '2', str(self.base / 'candidates.json'), 'n', 'n', 'n']
        with self.answers(values):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'done')
        self.assertEqual(tp.stats(self.db)['qualified'], 0)
        self.assertIn('researching', (Path(session['state']['output_dir']) / 'report.md').read_text(encoding='utf-8'))

    def test_manual_entry_and_icp_revision(self):
        first = ['无', 'Factory', 'Belt', 'Conveying', 'Germany', 'Machinery', 'Buyer',
                 'n', 'y', '', 'Better belt', '', '', '', '']
        with self.answers(first):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'approval')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM icp_profiles').fetchone()[0], 2)
        second = ['y', 'Alice', 'n', 'y', '1', 'Example GmbH', 'https://example.org',
                  'https://example.org/company', 'Germany', 'Machinery', 'About company', 'n',
                  'n', 'n', 'n']
        with self.answers(second):
            session = wizard.run(self.db, output=self.base / 'output')
        self.assertEqual(session['stage'], 'done')
        self.assertEqual(session['state']['icp_id'], 2)
        self.assertEqual(tp.stats(self.db)['companies'], 1)


if __name__ == '__main__':
    unittest.main()
