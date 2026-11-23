from fractions import Fraction
import json

from exchange.accept import receive_packet
from exchange.envelope import seal_v1
from exchange.issue import issue_packet
from procedure.conversion import ConversionSpec
from procedure.edit import DraftRule, ProcedureDraft
from procedure.freeze import freeze_procedure
from procedure.guards import RationalInterval
from reference.ingest import record_reference
from report.history import get_report
from tests.support import LabTestCase, REFERENCE_NAME, priority_draft, single_rule


class HandoverTests(LabTestCase):
    def test_offline_priority(self):
        record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '有证参考')
        packet = self.accept_draft(priority_draft())
        self.close_source()
        report = self.review(packet, 5)
        self.assert_decision(report, packet, 'pass', 'PRIMARY', Fraction(6))
        self.assertEqual(get_report(self.archive, report.report_id), report)

    def test_fractional_boundaries(self):
        record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '边界参考')
        closed = self.accept_draft(priority_draft())
        opened = self.accept_draft(priority_draft(upper_closed=False))
        self.close_source()
        for packet, deviation, rule, value in (
            (closed, '49/10', 'PRIMARY', Fraction(59, 10)),
            (closed, '5', 'PRIMARY', Fraction(6)),
            (closed, '51/10', 'BACKUP', Fraction(51, 5)),
            (opened, '49/10', 'PRIMARY', Fraction(59, 10)),
            (opened, '5', 'BACKUP', Fraction(10)),
            (opened, '51/10', 'BACKUP', Fraction(51, 5)),
        ):
            with self.subTest(packet=packet.packet_id, deviation=deviation):
                self.assert_decision(self.review(packet, deviation), packet, 'pass', rule, value)

    def test_explicit_packet_id(self):
        old = self.accept_draft(single_rule(scale=3))
        latest = self.accept_draft(single_rule(scale=4))
        self.assertNotEqual(old.procedure_id, latest.procedure_id)
        self.close_source()
        self.assert_decision(self.review(old, 2), old, 'pass', 'SINGLE', Fraction(6))
        self.assert_decision(self.review(latest, 2), latest, 'pass', 'SINGLE', Fraction(8))

    def test_same_value_provenance(self):
        constant = ConversionSpec(0, 6, 10)
        packet = self.accept_draft(ProcedureDraft((
            DraftRule('FIRST', RationalInterval(upper=5, upper_closed=True), constant),
            DraftRule('SECOND', RationalInterval(), constant),
        )))
        self.close_source()
        self.assert_decision(self.review(packet, 5), packet, 'pass', 'FIRST', Fraction(6))
        self.assert_decision(self.review(packet, '51/10'), packet, 'pass', 'SECOND', Fraction(6))

    def test_conditional_missing(self):
        packet = self.accept_draft(priority_draft())
        self.close_source()
        boundary = self.review(packet, 5)
        observed = boundary.decision
        if (observed.status, observed.rule_id, observed.value) == ('pass', 'BACKUP', Fraction(10)):
            self.fail('WRONG_FALLBACK: deviation 5 was released by BACKUP at 10 μm')
        self.assert_decision(boundary, packet, 'missing_evidence', 'PRIMARY', None)
        for deviation, status, rule, value in (
            ('49/10', 'missing_evidence', 'PRIMARY', None),
            ('51/10', 'pass', 'BACKUP', Fraction(51, 5)),
            ('7', 'pass', 'BACKUP', Fraction(14)),
            ('8', 'fail', 'BACKUP', Fraction(16)),
        ):
            with self.subTest(deviation=deviation):
                self.assert_decision(self.review(packet, deviation), packet, status, rule, value)
        self.assertEqual(get_report(self.archive, boundary.report_id), boundary)

    def test_producer_projection(self):
        procedure = freeze_procedure(self.source, priority_draft())
        document = json.loads(issue_packet(self.source, procedure.procedure_id).body)
        self.assertEqual(document['procedure_id'], procedure.procedure_id)
        self.assertEqual([rule['rule_id'] for rule in document['rules']], ['PRIMARY', 'BACKUP'])
        primary, backup = document['rules']
        self.assertIsNone(primary['conversion'])
        self.assertEqual(primary['interval'], {
            'lower': None, 'upper': '5', 'lower_closed': False, 'upper_closed': True,
        })
        upper = Fraction(primary['interval']['upper'])
        self.assertTrue(primary['interval']['upper_closed'] and Fraction(5) == upper)
        self.assertGreater(Fraction(7), upper)
        self.assertEqual(backup['conversion'], {'scale': '2', 'offset': '0', 'limit': '14', 'include_limit': True})

    def test_consumer_first_match(self):
        # Fixed values here are hand-derived from the two business rules.
        manual_body = {
            'procedure_id': 91,
            'rules': [
                {
                    'rule_id': 'PRIMARY',
                    'interval': {'lower': None, 'upper': '5', 'lower_closed': False, 'upper_closed': True},
                    'conversion': None,
                },
                {
                    'rule_id': 'BACKUP',
                    'interval': {'lower': None, 'upper': None, 'lower_closed': False, 'upper_closed': False},
                    'conversion': {'scale': '2', 'offset': '0', 'limit': '14', 'include_limit': True},
                },
            ],
        }
        packet = receive_packet(self.catalog, seal_v1(json.dumps(manual_body)))
        self.close_source()
        self.assertEqual(packet.procedure_id, 91)
        self.assert_decision(self.review(packet, 5), packet, 'missing_evidence', 'PRIMARY', None)
        self.assert_decision(self.review(packet, 7), packet, 'pass', 'BACKUP', Fraction(14))

    def test_quality_limit(self):
        packet = self.accept_draft(priority_draft())
        self.close_source()
        self.assert_decision(self.review(packet, 7), packet, 'pass', 'BACKUP', Fraction(14))
        self.assert_decision(self.review(packet, 8), packet, 'fail', 'BACKUP', Fraction(16))

    def test_old_packet_after_fill(self):
        old = self.accept_draft(priority_draft())
        original_report = self.review(old, 5)
        record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '补测参考')
        new = self.accept_draft(priority_draft())
        self.close_source()
        self.assert_decision(self.review(old, 5), old, 'missing_evidence', 'PRIMARY', None)
        self.assert_decision(self.review(new, 5), new, 'pass', 'PRIMARY', Fraction(6))
        self.reopen_archive()
        self.assertEqual(get_report(self.archive, original_report.report_id), original_report)
