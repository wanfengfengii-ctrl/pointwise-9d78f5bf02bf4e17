"""End-to-end disposition walkthroughs used by the fixture, verifier and demo.

Both scenarios use the four real frozen/issued/accepted V1 packets from
``disposition_fixture``.  Every connection is a file-backed SQLite database so
that "reopening" really proves durability across independent commits.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

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

DEVIATION = '2'


class Bench:
    """Owns file-backed stores for one scenario and can reopen them."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.catalog = None
        self.archive = None
        self.sorting_db = None
        self.desk = None

    def open(self, *, after_commit=None):
        self.catalog = sqlite3.connect(self.root / 'intake.db')
        self.archive = sqlite3.connect(self.root / 'reports.db')
        initialize_catalog(self.catalog)
        initialize_archive(self.archive)
        initialize_disposition_archive(self.archive)
        self.sorting_db = sqlite3.connect(self.root / 'sorting.db')
        self.desk = SortingDesk(self.sorting_db, after_commit=after_commit)
        with closing(sqlite3.connect(self.root / 'source.db')) as source:
            self.packets = prepare_examples(source, self.catalog)
        return self

    def reopen(self):
        for connection in (self.archive, self.sorting_db):
            if connection is not None:
                connection.close()
        self.archive = sqlite3.connect(self.root / 'reports.db')
        self.sorting_db = sqlite3.connect(self.root / 'sorting.db')
        self.desk = SortingDesk(self.sorting_db)
        return self

    def reopen_desk(self, *, after_commit=None):
        if self.sorting_db is not None:
            self.sorting_db.close()
        self.sorting_db = sqlite3.connect(self.root / 'sorting.db')
        self.desk = SortingDesk(self.sorting_db, after_commit=after_commit)
        return self

    def close(self):
        for connection in (self.catalog, self.archive, self.sorting_db):
            if connection is not None:
                connection.close()
        self.catalog = self.archive = self.sorting_db = None


def _ticket_report(ticket):
    return ticket['report_id']


def late_ticket_scenario(root: Path):
    """Open R6, upgrade to R8, then execute the original R6 ticket late."""
    bench = Bench(root).open()
    try:
        packets = bench.packets
        send = bench.desk.exchange
        opened = open_disposition(
            bench.catalog, bench.archive, send, 'a-open', 'A', packets['R6'].packet_id, DEVIATION,
        )
        upgraded = revise_disposition(
            bench.catalog, bench.archive, send, 'a-r8', 'A',
            opened['report_id'], packets['R8'].packet_id,
        )
        assert upgraded['status'] == 'signed'
        assert get_disposition(bench.archive, 'A') == {'report_id': upgraded['report_id'], 'pending': None}

        bench.desk.after_commit = DropReply('apply', 'a-execute')
        lost = False
        try:
            bench.desk.apply('a-execute', 'A', opened['ticket'])
        except TimeoutError:
            lost = True
        assert lost, 'the execution reply should be lost after commit'

        bench.reopen_desk()
        send = bench.desk.exchange
        execution = bench.desk.get_execution('A')
        assert execution is not None
        assert execution['ticket']['report_id'] == opened['report_id']
        assert execution['ticket']['category'] == 'release'

        conflict = revise_disposition(
            bench.catalog, bench.archive, send, 'a-i12', 'A',
            upgraded['report_id'], packets['I12'].packet_id,
        )
        assert conflict == {'status': 'conflict', 'report_id': upgraded['report_id']}
        assert get_disposition(bench.archive, 'A') == {'report_id': upgraded['report_id'], 'pending': None}

        corrected = revise_disposition(
            bench.catalog, bench.archive, send, 'a-r4', 'A',
            upgraded['report_id'], packets['R4'].packet_id,
        )
        assert corrected['status'] == 'signed'
        assert get_disposition(bench.archive, 'A')['report_id'] == corrected['report_id']

        # A specimen executes at most once: a newer same-category ticket still
        # returns the actual first execution.
        corrected_ticket = issue_disposition(bench.archive, send, 'A')
        again = bench.desk.apply('a-r4-execute', 'A', corrected_ticket)
        assert again is not None
        assert _ticket_report(again['ticket']) == opened['report_id']
        return {
            'opened': opened,
            'upgraded': upgraded,
            'execution': execution,
            'conflict': conflict,
            'corrected': corrected,
        }
    finally:
        bench.close()


def pending_revision_scenario(root: Path):
    """Lose the category-change reply, fail the retry sign, then finish."""
    bench = Bench(root).open(after_commit=DropReply('revise', 'b-i12'))
    try:
        packets = bench.packets
        send = bench.desk.exchange
        opened = open_disposition(
            bench.catalog, bench.archive, send, 'b-open', 'B', packets['R6'].packet_id, DEVIATION,
        )
        upgraded = revise_disposition(
            bench.catalog, bench.archive, send, 'b-r8', 'B',
            opened['report_id'], packets['R8'].packet_id,
        )
        assert upgraded['status'] == 'signed'
        upgraded_ticket = issue_disposition(bench.archive, send, 'B')
        assert upgraded_ticket['report_id'] == upgraded['report_id']

        lost = False
        try:
            revise_disposition(
                bench.catalog, bench.archive, send, 'b-i12', 'B',
                upgraded['report_id'], packets['I12'].packet_id,
            )
        except TimeoutError:
            lost = True
        assert lost, 'the category-change reply should be lost after commit'

        pending = get_disposition(bench.archive, 'B')
        assert pending == {'report_id': upgraded['report_id'], 'pending': 'b-i12'}

        # The sorting ledger already voided both old tickets.
        assert bench.desk.apply('b-r6', 'B', opened['ticket']) is None
        assert bench.desk.apply('b-r8', 'B', upgraded_ticket) is None

        bench.archive.execute('''
            CREATE TRIGGER deny_receipt BEFORE INSERT ON first_receipts
            BEGIN SELECT RAISE(ABORT, 'receipt storage denied'); END
        ''')
        sign_failed = False
        try:
            revise_disposition(
                bench.catalog, bench.archive, send, 'b-i12', 'B',
                upgraded['report_id'], packets['I12'].packet_id,
            )
        except sqlite3.IntegrityError:
            sign_failed = True
        assert sign_failed, 'the retry sign should fail on the first receipt'

        bench.reopen()
        assert get_disposition(bench.archive, 'B') == {
            'report_id': upgraded['report_id'], 'pending': 'b-i12',
        }
        assert get_report(bench.archive, upgraded['report_id']) is not None
        send = bench.desk.exchange

        bench.archive.execute('DROP TRIGGER deny_receipt')
        signed = revise_disposition(
            bench.catalog, bench.archive, send, 'b-i12', 'B',
            upgraded['report_id'], packets['I12'].packet_id,
        )
        assert signed['status'] == 'signed'
        assert get_disposition(bench.archive, 'B') == {'report_id': signed['report_id'], 'pending': None}

        final = revise_disposition(
            bench.catalog, bench.archive, send, 'b-r4', 'B',
            signed['report_id'], packets['R4'].packet_id,
        )
        assert final['status'] == 'signed'
        final_ticket = issue_disposition(bench.archive, send, 'B')
        for ticket, request in ((opened['ticket'], 'b-old-r6'), (upgraded_ticket, 'b-old-r8')):
            assert bench.desk.apply(request, 'B', ticket) is None
        executed = bench.desk.apply('b-r4-execute', 'B', final_ticket)
        assert executed is not None
        assert _ticket_report(executed['ticket']) == final['report_id']

        bench.reopen()
        reports = [
            get_report(bench.archive, report_id)
            for report_id in (opened['report_id'], upgraded['report_id'], signed['report_id'], final['report_id'])
        ]
        assert all(report is not None for report in reports)
        return {
            'opened': opened,
            'upgraded': upgraded,
            'signed': signed,
            'final': final,
            'execution': executed,
            'reports': reports,
        }
    finally:
        bench.close()
