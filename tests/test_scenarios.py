"""Documented end-to-end disposition scenarios as regression tests."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from disposition_scenarios import late_ticket_scenario, pending_revision_scenario
from report.history import get_report


class LateTicketScenarioTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='jin-heng-scenario-late-')
        self.addCleanup(self.directory.cleanup)
        self.outcome = late_ticket_scenario(Path(self.directory.name))

    def test_actual_execution_is_original_ticket(self):
        opened = self.outcome['opened']
        execution = self.outcome['execution']
        self.assertEqual(execution['ticket']['report_id'], opened['report_id'])
        self.assertEqual(execution['ticket']['category'], 'release')
        self.assertEqual(execution['request_id'], 'a-execute')

    def test_category_conflict_keeps_upgraded_report(self):
        upgraded = self.outcome['upgraded']
        self.assertEqual(self.outcome['conflict'],
                         {'status': 'conflict', 'report_id': upgraded['report_id']})

    def test_same_category_correction_signs(self):
        self.assertEqual(self.outcome['corrected']['status'], 'signed')
        self.assertGreater(self.outcome['corrected']['report_id'], self.outcome['upgraded']['report_id'])

    def test_old_reports_retrievable(self):
        with sqlite3.connect(Path(self.directory.name) / 'reports.db') as archive:
            for key in ('opened', 'upgraded', 'corrected'):
                report_id = self.outcome[key]['report_id']
                self.assertIsNotNone(get_report(archive, report_id), key)


class PendingRevisionScenarioTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='jin-heng-scenario-pending-')
        self.addCleanup(self.directory.cleanup)
        self.outcome = pending_revision_scenario(Path(self.directory.name))

    def test_resumed_request_matches_dropped_one(self):
        self.assertEqual(self.outcome['signed']['status'], 'signed')

    def test_final_and_execution(self):
        self.assertEqual(self.outcome['execution']['ticket']['report_id'],
                         self.outcome['final']['report_id'])

    def test_all_versions_retrievable(self):
        self.assertTrue(all(report is not None for report in self.outcome['reports']))
        report_ids = [report.report_id for report in self.outcome['reports']]
        self.assertEqual(report_ids, sorted(set(report_ids)))


if __name__ == '__main__':
    unittest.main()
