from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from exchange.accept import receive_packet
from exchange.catalog import initialize_catalog
from exchange.issue import issue_packet
from procedure.conversion import ConversionSpec
from procedure.edit import DraftRule, ProcedureDraft
from procedure.freeze import freeze_procedure, initialize_procedure_store
from procedure.guards import RationalInterval
from reference.revisions import initialize_reference_store
from report.archive import initialize_archive
from review.service import review_case


REFERENCE_NAME = 'private-probe-A'


def priority_draft(*, upper_closed=True):
    return ProcedureDraft((
        DraftRule(
            'PRIMARY',
            RationalInterval(upper=5, upper_closed=upper_closed),
            ConversionSpec(1, 0, 12, reference_factor='1/2'),
            reference_name=REFERENCE_NAME,
            allow_missing=True,
        ),
        DraftRule('BACKUP', RationalInterval(), ConversionSpec(2, 0, 14)),
    ))


def single_rule(scale=1, offset=0, limit=20, *, interval=None, include_limit=True):
    guard = RationalInterval() if interval is None else interval
    return ProcedureDraft((DraftRule('SINGLE', guard, ConversionSpec(scale, offset, limit, include_limit)),))


class LabTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='jin-heng-test-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source = self.open_store(root / 'source.db')
        self.catalog = self.open_store(root / 'intake.db')
        self.archive_path = root / 'reports.db'
        self.archive = self.open_store(self.archive_path)
        initialize_reference_store(self.source)
        initialize_procedure_store(self.source)
        initialize_catalog(self.catalog)
        initialize_archive(self.archive)

    def open_store(self, path):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        return connection

    def close_source(self):
        self.source.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.source.execute('SELECT 1')

    def reopen_archive(self):
        self.archive.close()
        self.archive = self.open_store(self.archive_path)

    def accept_draft(self, draft):
        frozen = freeze_procedure(self.source, draft)
        envelope = issue_packet(self.source, frozen.procedure_id)
        return receive_packet(self.catalog, envelope)

    def review(self, packet, deviation):
        return review_case(self.catalog, self.archive, packet.packet_id, deviation)

    def archive_counts(self):
        return self.archive.execute('''
            SELECT (SELECT count(*) FROM reports), (SELECT count(*) FROM first_receipts)
        ''').fetchone()

    def catalog_count(self):
        return self.catalog.execute('SELECT count(*) FROM accepted_packets').fetchone()[0]

    def assert_decision(self, report, packet, status, rule, value):
        self.assertEqual(
            (report.decision.status, report.decision.rule_id, report.decision.value),
            (status, rule, value),
        )
        self.assertEqual(report.decision.packet_id, packet.packet_id)
        self.assertEqual(report.decision.procedure_id, packet.procedure_id)
        self.assertEqual((report.provenance.procedure_id, report.provenance.rule_id), (packet.procedure_id, rule))
        self.assertEqual(report.first_receipt.report_id, report.report_id)
        self.assertEqual(report.first_receipt.packet_id, packet.packet_id)
        self.assertEqual(report.first_receipt.procedure_id, packet.procedure_id)
