from dataclasses import replace

from exchange.issue import issue_packet
from procedure.edit import ProcedureDraft
from procedure.freeze import freeze_procedure, load_frozen_procedure
from procedure.guards import RationalInterval
from reference.ingest import record_reference
from tests.support import LabTestCase, priority_draft, single_rule


class ProcedureTests(LabTestCase):
    def test_reversed_range(self):
        original = freeze_procedure(self.source, single_rule())
        with self.assertRaises(ValueError):
            freeze_procedure(self.source, single_rule(interval=RationalInterval(4, 3, True, True)))
        self.assertEqual(issue_packet(self.source, original.procedure_id).version, 1)
        next_revision = freeze_procedure(self.source, single_rule(scale=2))
        self.assertEqual(next_revision.procedure_id, original.procedure_id + 1)

    def test_allowed_missing(self):
        frozen = freeze_procedure(self.source, priority_draft())
        self.assertEqual(load_frozen_procedure(self.source, frozen.procedure_id), frozen)
        primary, backup = frozen.rules
        self.assertEqual((primary.rule_id, primary.interval.upper, primary.interval.upper_closed), ('PRIMARY', 5, True))
        self.assertIsNotNone(primary.evidence)
        self.assertIsNone(primary.evidence.revision_id)
        self.assertIsNone(primary.evidence.normalized)
        self.assertIsNone(backup.evidence)

    def test_unknown_revision(self):
        other = record_reference(self.source, 'another-probe', 2, 'μm', '另一条参考')
        primary, backup = priority_draft().rules
        for requested in (other.revision_id, other.revision_id + 10):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                freeze_procedure(self.source, ProcedureDraft((replace(primary, reference_revision=requested), backup)))
        self.assertEqual(self.source.execute('SELECT count(*) FROM procedure_revisions').fetchone()[0], 0)

    def test_rule_identity(self):
        original = freeze_procedure(self.source, priority_draft())
        primary, backup = priority_draft().rules
        with self.assertRaises(ValueError):
            freeze_procedure(self.source, ProcedureDraft((primary, replace(backup, rule_id=primary.rule_id))))
        self.assertEqual(load_frozen_procedure(self.source, original.procedure_id), original)
        self.assertEqual(issue_packet(self.source, original.procedure_id).version, 1)

    def test_required_reference_and_unknown_frozen_procedure(self):
        primary, backup = priority_draft().rules
        with self.assertRaises(ValueError):
            freeze_procedure(self.source, ProcedureDraft((replace(primary, allow_missing=False), backup)))
        with self.assertRaises(LookupError):
            issue_packet(self.source, 1)
