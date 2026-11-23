from dataclasses import replace
from fractions import Fraction

from procedure.edit import ProcedureDraft
from procedure.freeze import freeze_procedure, initialize_procedure_store, load_frozen_procedure
from reference.history import get_current_revision, get_reference_revision
from reference.ingest import record_reference
from reference.revisions import initialize_reference_store
from tests.support import LabTestCase, REFERENCE_NAME, priority_draft


class ReferenceTests(LabTestCase):
    def test_exact_units(self):
        first = record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '台架原记录')
        frozen = freeze_procedure(self.source, priority_draft())
        second = record_reference(self.source, REFERENCE_NAME, 3000, 'nm', '台架更正', replaces=first.revision_id)
        self.assertEqual(first.normalized, Fraction(2))
        self.assertEqual(second.normalized, Fraction(3))
        self.assertEqual((first.reading.raw_value, first.reading.unit), (2000, 'nm'))
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), second)
        self.assertEqual(get_reference_revision(self.source, REFERENCE_NAME, first.revision_id), first)
        evidence = load_frozen_procedure(self.source, frozen.procedure_id).rules[0].evidence
        self.assertEqual((evidence.revision_id, evidence.normalized, evidence.provenance), (first.revision_id, 2, '台架原记录'))

    def test_pinned_history(self):
        first = record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '旧台架')
        second = record_reference(self.source, REFERENCE_NAME, 3000, 'nm', '新台架', replaces=first.revision_id)
        primary, backup = priority_draft().rules
        pinned = ProcedureDraft((replace(primary, reference_revision=first.revision_id), backup))
        frozen = freeze_procedure(self.source, pinned)
        evidence = frozen.rules[0].evidence
        self.assertEqual((evidence.revision_id, evidence.normalized, evidence.provenance), (first.revision_id, 2, '旧台架'))
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), second)
        packet = self.accept_draft(pinned)
        self.close_source()
        self.assert_decision(self.review(packet, 5), packet, 'pass', 'PRIMARY', Fraction(6))

    def test_zero_reference(self):
        zero = record_reference(self.source, REFERENCE_NAME, 0, 'nm', '零位标定')
        draft = priority_draft()
        evidence = freeze_procedure(self.source, draft).rules[0].evidence
        self.assertEqual((evidence.revision_id, evidence.normalized), (zero.revision_id, 0))
        packet = self.accept_draft(draft)
        self.close_source()
        self.assert_decision(self.review(packet, 5), packet, 'pass', 'PRIMARY', Fraction(5))

    def test_reinit_source(self):
        first = record_reference(self.source, REFERENCE_NAME, '3/2', 'μm', '初测')
        frozen = freeze_procedure(self.source, priority_draft())
        second = record_reference(self.source, REFERENCE_NAME, 2, 'μm', '更正', replaces=first.revision_id)
        for _ in range(2):
            initialize_reference_store(self.source)
            initialize_procedure_store(self.source)
        self.assertEqual(get_reference_revision(self.source, REFERENCE_NAME, first.revision_id), first)
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), second)
        self.assertEqual(load_frozen_procedure(self.source, frozen.procedure_id), frozen)

    def test_bad_unit(self):
        original = record_reference(self.source, REFERENCE_NAME, 2000, 'nm', '台架原记录')
        with self.assertRaises(ValueError):
            record_reference(self.source, REFERENCE_NAME, 3000, 'unknown', '无效更正', replaces=original.revision_id)
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), original)
        corrected = record_reference(self.source, REFERENCE_NAME, 3000, 'nm', '有效更正', replaces=original.revision_id)
        self.assertEqual(corrected.revision_id, original.revision_id + 1)

    def test_bad_reading(self):
        original = record_reference(self.source, REFERENCE_NAME, 1, 'μm', '基准')
        for value in ('1/0', 'unknown', 0.1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                record_reference(self.source, REFERENCE_NAME, value, 'μm', '错误录入', replaces=original.revision_id)
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), original)
        corrected = record_reference(self.source, REFERENCE_NAME, '1.5', 'μm', '有效更正', replaces=original.revision_id)
        self.assertEqual((corrected.revision_id, corrected.normalized), (original.revision_id + 1, Fraction(3, 2)))

    def test_correction_requires_current_revision(self):
        first = record_reference(self.source, REFERENCE_NAME, 1, 'μm', '初测')
        with self.assertRaises(ValueError):
            record_reference(self.source, REFERENCE_NAME, 2, 'μm', '重复登记')
        second = record_reference(self.source, REFERENCE_NAME, 2, 'μm', '更正', replaces=first.revision_id)
        with self.assertRaises(ValueError):
            record_reference(self.source, REFERENCE_NAME, 3, 'μm', '迟到更正', replaces=first.revision_id)
        self.assertEqual(get_current_revision(self.source, REFERENCE_NAME), second)
