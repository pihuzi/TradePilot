import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tradepilot as tp


class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = tp.connect(str(Path(self.tmp.name) / 'test.db'))
        self.icp = tp.icp_create(self.db, {'supplier': 'Factory', 'product': 'Industrial belt', 'application': 'Conveying', 'countries': ['Germany'], 'industries': ['Machinery'], 'roles': ['Procurement']})['icp_id']

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def prospect(self):
        tp.approve_icp(self.db, self.icp, 'Alice')
        data = {'name': 'Example GmbH', 'website': 'https://example.org', 'source_url': 'https://directory.example.org/ex', 'country': 'Germany', 'industry': 'Machinery'}
        cid = tp.discover(self.db, self.icp, [data, {**data, 'name': 'Duplicate'}])[0]['company_id']
        tp.add_evidence(self.db, cid, {'claim': 'Website describes manufacturing', 'excerpt': 'Machinery and conveyors', 'source_url': 'https://example.org/products', 'source_type': 'official_website'})
        tp.add_evidence(self.db, cid, {'claim': 'Job advertisement', 'excerpt': 'Hiring production engineer', 'source_url': 'https://example.org/jobs', 'source_type': 'hiring'})
        for evidence in self.db.execute("SELECT id FROM evidence_records WHERE company_id=? AND source_type IN ('official_website','hiring')", (cid,)).fetchall():
            tp.verify_evidence(self.db, evidence['id'], 'Alice')
        self.db.commit()
        return cid

    def test_full_workflow_and_guardrails(self):
        with self.assertRaises(ValueError):
            tp.discover(self.db, self.icp, [])
        cid = self.prospect()
        self.assertEqual(tp.stats(self.db)['companies'], 1)
        assessment = tp.assess(self.db, cid)
        self.assertEqual(assessment['score'], 100)
        self.assertEqual(assessment['decision'], 'pending_review')
        with self.assertRaises(ValueError):
            tp.enrichment(self.db, cid, 'Vendor', 'contact', 1, 5, 'Alice')
        tp.qualify(self.db, cid, 'Alice', 'qualified')
        self.assertEqual(tp.enrichment(self.db, cid, 'Vendor', 'contact', 1, 5, 'Alice')['actual_cost'], 0)
        person = tp.contact(self.db, cid, {'job_title': 'Purchasing Manager', 'email': 'buyer@example.org', 'source_url': 'https://example.org/team'})['contact_id']
        draft = tp.draft(self.db, cid, person, 'Industrial belts', 'Hello, would you like to discuss industrial belts?')['draft_id']
        with self.assertRaises(ValueError):
            tp.send(self.db, draft, 20)
        tp.approve_draft(self.db, draft, 'Alice')
        with self.assertRaises(ValueError):
            tp.send(self.db, draft, 20)  # no SMTP configured; no fictitious send
        self.assertEqual(tp.stats(self.db)['sent'], 0)
        tp.feedback(self.db, cid, 'unsubscribe', 'No outreach')
        with self.assertRaises(ValueError):
            tp.send(self.db, draft, 20)
        self.assertEqual(len(tp.report(self.db, cid)['evidence']), 3)
        self.assertEqual(tp.stats(self.db)['qualified'], 1)

    def test_unverified_signals_do_not_qualify(self):
        tp.approve_icp(self.db, self.icp, 'Alice')
        cid = tp.discover(self.db, self.icp, [{'name': 'Other', 'website': 'https://other.example', 'source_url': 'https://directory.example/other', 'country': 'Germany', 'industry': 'Machinery'}])[0]['company_id']
        self.assertEqual(tp.assess(self.db, cid)['decision'], 'researching')
        with self.assertRaises(ValueError):
            tp.qualify(self.db, cid, 'Alice', 'qualified')
        with self.assertRaises(ValueError):
            tp.add_evidence(self.db, cid, {'claim': 'Claim', 'excerpt': 'Text', 'source_url': 'file:///etc/passwd'})

    def test_icp_versioning_and_low_score_cost_gate(self):
        second = tp.icp_create(self.db, {'supplier': 'Factory', 'product': 'Belt', 'application': 'Conveying', 'countries': ['France'], 'industries': ['Industry'], 'roles': ['Buyer']})
        self.assertEqual(second['version'], 2)
        cid = self.prospect()
        self.assertEqual(tp.report(self.db, cid)['company']['icp_id'], self.icp)
        with self.assertRaises(ValueError):
            tp.enrichment(self.db, cid, 'Vendor', 'contact', 10, 1, 'Alice')

    def test_search_hits_are_not_company_records(self):
        tp.approve_icp(self.db, self.icp, 'Alice')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b'<rss><channel><item><title>Dictionary</title><link>https://example.org/entry</link><description>Not a company</description></item></channel></rss>'
        with patch.object(tp.urllib.request.OpenerDirector, 'open', return_value=Response()):
            hits = tp.search_candidates(self.db, self.icp, 2)
        self.assertEqual(hits['search_hits'][0]['status'], 'unverified_search_hit')
        self.assertEqual(tp.stats(self.db)['companies'], 0)

    def test_inference_cannot_be_verified(self):
        cid = self.prospect()
        tp.add_evidence(self.db, cid, {'claim': 'Possible purchase', 'excerpt': 'Possibly purchasing', 'source_url': 'https://example.org/speculation', 'status': 'inferred'})
        identifier = self.db.execute("SELECT id FROM evidence_records WHERE company_id=? AND status='inferred'", (cid,)).fetchone()[0]
        with self.assertRaises(ValueError):
            tp.verify_evidence(self.db, identifier, 'Alice')
        with self.assertRaises(ValueError):
            tp.add_evidence(self.db, cid, {'claim': 'Forged approval', 'excerpt': 'No', 'source_url': 'https://example.org/forged', 'verification': 'verified'})


if __name__ == '__main__':
    unittest.main()
