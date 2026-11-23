"""Tests for disposition opening, revision, issuing and sorting execution."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from disposition_fixture import DropReply, prepare_examples
from exchange.catalog import initialize_catalog
from report.archive import initialize_archive, initialize_disposition_archive
from report.history import get_report
from review.service import (
    get_disposition,
    issue_disposition,
    open_disposition,
    revise_disposition,
)
from sorting import SortingDesk

RELEASE = 'release'
ISOLATE = 'isolate'


class DispositionTestCase(unittest.TestCase):
    def accept(self, draft):
        from exchange.accept import receive_packet
        from exchange.issue import issue_packet
        from procedure.freeze import freeze_procedure
        frozen = freeze_procedure(self.source, draft)
        return receive_packet(self.catalog, issue_packet(self.source, frozen.procedure_id))

    def setUp(self):
        directory = TemporaryDirectory(prefix='jin-heng-disposition-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.catalog = sqlite3.connect(self.root / 'intake.db')
        self.addCleanup(self.catalog.close)
        self.archive = sqlite3.connect(self.root / 'reports.db')
        self.addCleanup(self.archive.close)
        self.sorting_db = sqlite3.connect(self.root / 'sorting.db')
        self.addCleanup(self.sorting_db.close)
        initialize_catalog(self.catalog)
        initialize_archive(self.archive)
        initialize_disposition_archive(self.archive)
        self.desk = SortingDesk(self.sorting_db)
        from procedure.freeze import initialize_procedure_store
        from reference.revisions import initialize_reference_store
        self.source = sqlite3.connect(self.root / 'source.db')
        self.addCleanup(self.source.close)
        initialize_reference_store(self.source)
        initialize_procedure_store(self.source)
        initialize_catalog(self.catalog)
        self.packets = prepare_examples(self.source, self.catalog)
        self.send = self.desk.exchange

    def reopen(self):
        self.archive.close()
        self.sorting_db.close()
        self.archive = sqlite3.connect(self.root / 'reports.db')
        self.sorting_db = sqlite3.connect(self.root / 'sorting.db')
        self.addCleanup(self.archive.close)
        self.addCleanup(self.sorting_db.close)
        self.desk = SortingDesk(self.sorting_db)
        self.send = self.desk.exchange

    def open(self, request_id='open', specimen='A', rule='R6'):
        return open_disposition(
            self.catalog, self.archive, self.send, request_id, specimen,
            self.packets[rule].packet_id, '2',
        )

    def revise(self, request_id, specimen, parent, rule):
        return revise_disposition(
            self.catalog, self.archive, self.send, request_id, specimen,
            parent, self.packets[rule].packet_id,
        )

    def report_count(self):
        return self.archive.execute('SELECT count(*) FROM reports').fetchone()[0]

    def request_rows(self, status=None):
        if status is None:
            return self.archive.execute('SELECT count(*) FROM disposition_requests').fetchone()[0]
        return self.archive.execute(
            'SELECT count(*) FROM disposition_requests WHERE status = ?', (status,)
        ).fetchone()[0]


class OpenTests(DispositionTestCase):
    def test_open_release(self):
        result = self.open()
        self.assertEqual(result['ticket']['category'], RELEASE)
        self.assertEqual(result['ticket']['report_id'], result['report_id'])
        self.assertEqual(result['ticket']['specimen'], 'A')
        self.assertEqual(get_disposition(self.archive, 'A'), {'report_id': result['report_id'], 'pending': None})
        # The ticket is delivered and executable exactly once.
        executed = self.desk.apply('exec', 'A', result['ticket'])
        self.assertEqual(executed['request_id'], 'exec')
        self.assertEqual(executed['ticket']['report_id'], result['report_id'])
        self.assertEqual(self.desk.get_execution('A'), executed)

    def test_open_uses_fixed_deviation(self):
        result = self.open(rule='R4')
        self.assertEqual(result['ticket']['category'], RELEASE)
        self.assertEqual(result['ticket']['deviation'], '2')
        view = get_report(self.archive, result['report_id'])
        self.assertEqual((view.decision.status, view.decision.rule_id, view.decision.value),
                         ('pass', 'R4', 4))

    def test_missing_evidence_refused(self):
        from procedure.conversion import ConversionSpec
        from procedure.edit import DraftRule, ProcedureDraft
        from procedure.guards import RationalInterval
        draft = ProcedureDraft((DraftRule(
            'PRIMARY', RationalInterval(upper=2, upper_closed=True),
            ConversionSpec(1, 0, 10, reference_factor=1),
            reference_name='missing-probe', allow_missing=True,
        ),))
        packet = self.accept(draft)
        with self.assertRaises(ValueError):
            open_disposition(self.catalog, self.archive, self.send, 'x', 'X', packet.packet_id, '2')
        self.assertEqual(self.report_count(), 0)
        self.assertIsNone(get_disposition(self.archive, 'X'))

    def test_no_rule_refused(self):
        from procedure.conversion import ConversionSpec
        from procedure.edit import DraftRule, ProcedureDraft
        from procedure.guards import RationalInterval
        packet = self.accept(ProcedureDraft((
            DraftRule('BAND', RationalInterval(0, 1, True, True), ConversionSpec(1, 0, 10)),
        )))
        with self.assertRaises(ValueError):
            open_disposition(self.catalog, self.archive, self.send, 'x', 'X', packet.packet_id, '2')
        self.assertEqual(self.report_count(), 0)

    def test_unknown_packet(self):
        with self.assertRaises(LookupError):
            open_disposition(self.catalog, self.archive, self.send, 'x', 'X', 999, '2')
        self.assertEqual(self.report_count(), 0)

    def test_cannot_open_twice(self):
        first = self.open()
        with self.assertRaises(ValueError):
            self.open(request_id='again', specimen='A')
        self.assertEqual(self.report_count(), 1)
        self.assertEqual(get_disposition(self.archive, 'A')['report_id'], first['report_id'])

    def test_reopen_open_reply(self):
        first = self.open()
        again = self.open()
        self.assertEqual(again, first)
        self.assertEqual(self.report_count(), 1)

    def test_open_invalid_params(self):
        for request_id, specimen, deviation in (('', 'A', '2'), ('r', '', '2'),
                                                ('r', 'A', 'NaN'), ('r', 'A', '1/0')):
            with self.subTest(request_id=request_id, specimen=specimen, deviation=deviation):
                with self.assertRaises(ValueError):
                    open_disposition(self.catalog, self.archive, self.send, request_id, specimen,
                                     self.packets['R6'].packet_id, deviation)
        self.assertEqual(self.report_count(), 0)


class RevisionTests(DispositionTestCase):
    def test_same_category_keeps_old_ticket(self):
        opened = self.open()
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        self.assertEqual(revised['status'], 'signed')
        self.assertGreater(revised['report_id'], opened['report_id'])
        # The old ticket is still executable.
        executed = self.desk.apply('old', 'A', opened['ticket'])
        self.assertEqual(executed['ticket']['report_id'], opened['report_id'])
        # And the specimen has executed only once: a later ticket redelivers
        # the actual first execution.
        repeated = self.desk.apply('new', 'A', issue_disposition(self.archive, self.send, 'A'))
        self.assertEqual(repeated, executed)

    def test_category_change_voids_old_ticket(self):
        opened = self.open()
        revised = self.revise('i12', 'A', opened['report_id'], 'I12')
        self.assertEqual(revised['status'], 'signed')
        self.assertEqual(issue_disposition(self.archive, self.send, 'A')['category'], ISOLATE)
        self.assertIsNone(self.desk.apply('late', 'A', opened['ticket']))

    def test_return_to_original_category_does_not_restore(self):
        opened = self.open()
        self.revise('i12', 'A', opened['report_id'], 'I12')
        current = get_disposition(self.archive, 'A')['report_id']
        revised = self.revise('r4', 'A', current, 'R4')
        self.assertEqual(revised['status'], 'signed')
        self.assertIsNone(self.desk.apply('late', 'A', opened['ticket']))

    def test_executed_blocks_category_change(self):
        opened = self.open()
        self.desk.apply('first', 'A', opened['ticket'])
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        self.assertEqual(revised, {'status': 'signed', 'report_id': revised['report_id']})
        blocked = self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(blocked, {'status': 'conflict', 'report_id': revised['report_id']})
        self.assertEqual(get_disposition(self.archive, 'A')['report_id'], revised['report_id'])
        # Same-category correction is still allowed.
        corrected = self.revise('r4', 'A', revised['report_id'], 'R4')
        self.assertEqual(corrected['status'], 'signed')

    def test_parent_must_be_current(self):
        opened = self.open()
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        with self.assertRaises(ValueError):
            self.revise('stale', 'A', opened['report_id'], 'R4')
        self.assertEqual(get_disposition(self.archive, 'A')['report_id'], revised['report_id'])

    def test_unknown_specimen(self):
        with self.assertRaises(LookupError):
            self.revise('r', 'ghost', 1, 'R8')

    def test_different_params_rejected(self):
        opened = self.open()
        self.revise('r8', 'A', opened['report_id'], 'R8')
        with self.assertRaises(ValueError):
            self.revise('r8', 'A', opened['report_id'], 'R4')
        self.assertEqual(self.request_rows(), 2)

    def test_reopen_revision_same_params(self):
        opened = self.open()
        first = self.revise('r8', 'A', opened['report_id'], 'R8')
        again = self.revise('r8', 'A', opened['report_id'], 'R8')
        self.assertEqual(again, first)
        self.assertEqual(self.report_count(), 2)

    def test_reopen_conflict_same_params(self):
        opened = self.open()
        self.desk.apply('exec', 'A', opened['ticket'])
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        first = self.revise('i12', 'A', revised['report_id'], 'I12')
        again = self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(first, again)
        self.assertEqual(first['status'], 'conflict')


class LostReplyTests(DispositionTestCase):
    def test_lost_apply_reply_reopens(self):
        opened = self.open()
        self.desk.after_commit = DropReply('apply', 'exec')
        with self.assertRaises(TimeoutError):
            self.desk.apply('exec', 'A', opened['ticket'])
        self.reopen()
        execution = self.desk.get_execution('A')
        self.assertEqual(execution['ticket']['report_id'], opened['report_id'])
        # Replaying the same request returns the committed actual execution.
        replay = self.desk.apply('exec', 'A', opened['ticket'])
        self.assertEqual(replay, execution)
        self.reopen()
        self.assertEqual(self.desk.get_execution('A'), execution)

    def test_lost_revise_reply_then_sign_failure(self):
        opened = self.open()
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        self.desk.after_commit = DropReply('revise', 'i12')
        with self.assertRaises(TimeoutError):
            self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(get_disposition(self.archive, 'A'),
                         {'report_id': revised['report_id'], 'pending': 'i12'})
        # Other revisions are suspended; the old report stays retrievable and
        # its ticket issuable, but the sorting ledger already voided it.
        with self.assertRaises(ValueError):
            self.revise('r4', 'A', revised['report_id'], 'R4')
        self.assertIsNotNone(get_report(self.archive, revised['report_id']))
        old_ticket = issue_disposition(self.archive, self.send, 'A')
        self.assertEqual(old_ticket['report_id'], revised['report_id'])
        self.assertIsNone(self.desk.apply('old-issued', 'A', old_ticket))

        # The old tickets are already void in the sorting ledger.
        self.assertIsNone(self.desk.apply('old', 'A', opened['ticket']))

        self.reopen()
        self.archive.execute('''
            CREATE TRIGGER deny_receipt BEFORE INSERT ON first_receipts
            BEGIN SELECT RAISE(ABORT, 'receipt storage denied'); END
        ''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.revise('i12', 'A', revised['report_id'], 'I12')
        self.reopen()
        self.assertEqual(get_disposition(self.archive, 'A'),
                         {'report_id': revised['report_id'], 'pending': 'i12'})
        self.assertIsNotNone(get_report(self.archive, revised['report_id']))

        self.archive.execute('DROP TRIGGER deny_receipt')
        settled = self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(settled['status'], 'signed')
        self.assertEqual(get_disposition(self.archive, 'A'),
                         {'report_id': settled['report_id'], 'pending': None})

        final = self.revise('r4', 'A', settled['report_id'], 'R4')
        self.assertEqual(final['status'], 'signed')
        self.assertIsNone(self.desk.apply('old', 'A', opened['ticket']))
        executed = self.desk.apply('exec', 'A', issue_disposition(self.archive, self.send, 'A'))
        self.assertEqual(executed['ticket']['report_id'], final['report_id'])

    def test_lost_conflict_reply_resumes_as_conflict(self):
        opened = self.open()
        self.desk.apply('exec', 'A', opened['ticket'])
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        self.desk.after_commit = DropReply('revise', 'i12')
        with self.assertRaises(TimeoutError):
            self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(get_disposition(self.archive, 'A'),
                         {'report_id': revised['report_id'], 'pending': 'i12'})
        self.reopen()
        settled = self.revise('i12', 'A', revised['report_id'], 'I12')
        self.assertEqual(settled, {'status': 'conflict', 'report_id': revised['report_id']})
        self.assertEqual(get_disposition(self.archive, 'A'),
                         {'report_id': revised['report_id'], 'pending': None})

    def test_reissue_does_not_restore_void(self):
        opened = self.open()
        self.revise('i12', 'A', opened['report_id'], 'I12')
        self.reopen()
        self.assertIsNone(self.desk.apply('late', 'A', opened['ticket']))


class ReinitTests(DispositionTestCase):
    def test_reinit_preserves(self):
        opened = self.open()
        revised = self.revise('r8', 'A', opened['report_id'], 'R8')
        initialize_disposition_archive(self.archive)
        SortingDesk(self.sorting_db)
        self.assertEqual(get_disposition(self.archive, 'A')['report_id'], revised['report_id'])
        self.assertEqual(get_report(self.archive, opened['report_id']).report_id, opened['report_id'])

    def test_get_unknown(self):
        self.assertIsNone(get_disposition(self.archive, 'A'))
        with self.assertRaises(LookupError):
            issue_disposition(self.archive, self.send, 'A')


if __name__ == '__main__':
    unittest.main()
