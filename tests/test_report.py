from fractions import Fraction
import sqlite3

from reference.ingest import record_reference
from report.archive import initialize_archive
from report.history import get_report
from tests.support import LabTestCase, REFERENCE_NAME, priority_draft, single_rule


class ReportTests(LabTestCase):
    def test_failed_sign_retry(self):
        packet = self.accept_draft(single_rule(scale=2))
        self.close_source()
        self.archive.execute('''
            CREATE TRIGGER deny_report BEFORE INSERT ON reports
            BEGIN SELECT RAISE(ABORT, 'report storage denied'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'report storage denied'):
            self.review(packet, 3)
        self.assertEqual(self.archive_counts(), (0, 0))
        self.assertFalse(self.archive.in_transaction)
        self.archive.execute('DROP TRIGGER deny_report')
        result = self.review(packet, 3)
        self.assert_decision(result, packet, 'pass', 'SINGLE', Fraction(6))
        self.assertEqual(self.archive_counts(), (1, 1))
        self.assertEqual(get_report(self.archive, result.report_id), result)

    def test_receipt_write_failure(self):
        packet = self.accept_draft(single_rule(scale=2))
        self.close_source()
        self.archive.execute('''
            CREATE TRIGGER deny_receipt BEFORE INSERT ON first_receipts
            WHEN EXISTS (SELECT 1 FROM reports WHERE report_id = NEW.report_id)
            BEGIN SELECT RAISE(ABORT, 'receipt storage denied'); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt storage denied'):
            self.review(packet, 3)
        self.reopen_archive()
        self.assertEqual(self.archive_counts(), (0, 0))
        self.archive.execute('DROP TRIGGER deny_receipt')
        result = self.review(packet, 3)
        self.assert_decision(result, packet, 'pass', 'SINGLE', Fraction(6))
        self.assertEqual(self.archive_counts(), (1, 1))
        self.assertEqual(get_report(self.archive, result.report_id), result)

    def test_deferred_commit_failure(self):
        packet = self.accept_draft(single_rule())
        self.close_source()
        self.archive.execute('''
            CREATE TABLE commit_guard (
                missing_parent INTEGER REFERENCES reports (report_id) DEFERRABLE INITIALLY DEFERRED
            )
        ''')
        self.archive.execute('''
            CREATE TRIGGER fail_at_commit AFTER INSERT ON first_receipts
            BEGIN INSERT INTO commit_guard VALUES (-1); END
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            self.review(packet, 3)
        self.assertFalse(self.archive.in_transaction)
        self.reopen_archive()
        self.assertEqual(self.archive_counts(), (0, 0))
        self.assertEqual(self.archive.execute('SELECT count(*) FROM commit_guard').fetchone()[0], 0)
        self.archive.execute('DROP TRIGGER fail_at_commit')
        initialize_archive(self.archive)
        result = self.review(packet, 3)
        self.assert_decision(result, packet, 'pass', 'SINGLE', Fraction(3))
        self.assertEqual(self.archive_counts(), (1, 1))

    def test_reopen_old_result(self):
        first = record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '首轮台架')
        old_packet = self.accept_draft(priority_draft())
        original = self.review(old_packet, 5)
        record_reference(self.source, REFERENCE_NAME, 3000, 'nm', '更正台架', replaces=first.revision_id)
        new_packet = self.accept_draft(priority_draft())
        self.close_source()
        new_report = self.review(new_packet, 5)
        self.assert_decision(new_report, new_packet, 'pass', 'PRIMARY', Fraction(13, 2))
        self.catalog.close()
        self.reopen_archive()
        saved = get_report(self.archive, original.report_id)
        self.assertEqual(saved, original)
        self.assert_decision(saved, old_packet, 'pass', 'PRIMARY', Fraction(6))

    def test_reopen_first_receipt(self):
        old = self.accept_draft(priority_draft())
        original = self.review(old, 5)
        record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '补测')
        new = self.accept_draft(priority_draft())
        self.close_source()
        later = self.review(new, 5)
        self.assertNotEqual(original.first_receipt.receipt_id, later.first_receipt.receipt_id)
        self.reopen_archive()
        for _ in range(2):
            saved = get_report(self.archive, original.report_id)
            self.assertEqual(saved.first_receipt, original.first_receipt)
            self.assertEqual(saved, original)
        self.assertEqual(self.archive_counts(), (2, 2))

    def test_reinit_archive(self):
        packet = self.accept_draft(single_rule())
        self.close_source()
        result = self.review(packet, 4)
        self.reopen_archive()
        initialize_archive(self.archive)
        initialize_archive(self.archive)
        self.assertEqual(get_report(self.archive, result.report_id), result)
        self.assertEqual(self.archive_counts(), (1, 1))

    def test_unknown_report(self):
        self.assertIsNone(get_report(self.archive, 1))
        self.assertEqual(self.archive_counts(), (0, 0))
