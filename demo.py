from contextlib import closing
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory

from exchange.accept import receive_packet
from exchange.catalog import initialize_catalog
from exchange.issue import issue_packet
from procedure.conversion import ConversionSpec
from procedure.edit import DraftRule, ProcedureDraft
from procedure.freeze import freeze_procedure, initialize_procedure_store
from procedure.guards import RationalInterval, format_rational
from reference.ingest import record_reference
from reference.revisions import initialize_reference_store
from report.archive import initialize_archive
from report.history import get_report
from review.service import review_case


def shift_draft(reference_name):
    return ProcedureDraft((
        DraftRule(
            'PRIMARY', RationalInterval(upper=5, upper_closed=True),
            ConversionSpec(1, 0, 12, reference_factor='1/2'),
            reference_name=reference_name, allow_missing=True,
        ),
        DraftRule('BACKUP', RationalInterval(), ConversionSpec(2, 0, 14)),
    ))


def _result(report):
    decision = report.decision
    return {
        'deviation': format_rational(decision.deviation),
        'status': decision.status.value,
        'rule': decision.rule_id,
        'value': None if decision.value is None else format_rational(decision.value),
    }


def _print_scenario(inputs, reports, reopened, old_packet):
    expected = [
        {'deviation': '5', 'status': 'missing_evidence', 'rule': 'PRIMARY', 'value': None},
        {'deviation': '7', 'status': 'pass', 'rule': 'BACKUP', 'value': '14'},
        {'deviation': '8', 'status': 'fail', 'rule': 'BACKUP', 'value': '16'},
    ]
    actual = [_result(report) for report in reports]
    assert actual == expected, actual
    original = reports[0]
    assert reopened == original
    downstream_expected = {
        'report': original.report_id,
        'packet': old_packet.packet_id,
        'procedure': old_packet.procedure_id,
        'rule': 'PRIMARY',
        'receipt': original.first_receipt.receipt_id,
    }
    downstream_actual = {
        'report': reopened.report_id,
        'packet': reopened.first_receipt.packet_id,
        'procedure': reopened.provenance.procedure_id,
        'rule': reopened.provenance.rule_id,
        'receipt': reopened.first_receipt.receipt_id,
    }
    assert downstream_actual == downstream_expected
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v5',
        'input': {'deviations': inputs, 'packet': old_packet.packet_id},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['exchange/issue.py', 'exchange/accept.py', 'review/decision.py', 'report/history.py'],
        'oracle': '5 属于首条闭端且该参考缺失。7 和 8 手算两倍为 14 和 16，与闭合格限 14 比较。领取与签出时的原号及实收父件对照。',
    }, ensure_ascii=False, sort_keys=True))


def main():
    sample = json.loads((Path(__file__).parent / 'samples' / 'shift.json').read_text(encoding='utf-8'))
    bench = sample['bench_reference']
    supplement = sample['supplement']
    with TemporaryDirectory(prefix='jin-heng-demo-') as directory:
        root = Path(directory)
        source_path = root / 'source.db'
        archive_path = root / 'reports.db'
        with closing(sqlite3.connect(root / 'intake.db')) as catalog, closing(sqlite3.connect(archive_path)) as archive:
            initialize_catalog(catalog)
            initialize_archive(archive)
            with closing(sqlite3.connect(source_path)) as source:
                initialize_reference_store(source)
                initialize_procedure_store(source)
                initial = record_reference(source, bench['name'], bench['initial_reading'], bench['unit'], bench['initial_provenance'])
                old_procedure = freeze_procedure(source, shift_draft(supplement['name']))
                old_packet = receive_packet(catalog, issue_packet(source, old_procedure.procedure_id))
            first_shift = [review_case(catalog, archive, old_packet.packet_id, value) for value in sample['deviations']]

            with closing(sqlite3.connect(source_path)) as source:
                corrected = record_reference(
                    source, bench['name'], bench['corrected_reading'], bench['unit'],
                    bench['corrected_provenance'], replaces=initial.revision_id,
                )
                record_reference(source, supplement['name'], supplement['reading'], supplement['unit'], supplement['provenance'])
                new_procedure = freeze_procedure(source, shift_draft(supplement['name']))
                new_packet = receive_packet(catalog, issue_packet(source, new_procedure.procedure_id))
            old_again = review_case(catalog, archive, old_packet.packet_id, 5)
            new_result = review_case(catalog, archive, new_packet.packet_id, 5)
            assert (initial.normalized, corrected.normalized) == (2, 3)
            assert old_again.decision == first_shift[0].decision
            assert _result(new_result) == {'deviation': '5', 'status': 'pass', 'rule': 'PRIMARY', 'value': '6'}
            assert new_result.provenance.procedure_id == new_procedure.procedure_id

        with closing(sqlite3.connect(archive_path)) as archive:
            original = get_report(archive, first_shift[0].report_id)
            assert get_report(archive, new_result.report_id) == new_result
            _print_scenario(sample['deviations'], first_shift, original, old_packet)
            print(f'台架归一 {initial.normalized} μm → {corrected.normalized} μm')
            print(f'补测后使用实收件 {new_packet.packet_id}，偏差 5 的换算值为 {new_result.decision.value} μm')
            print(f'重开领取报告 {original.report_id}，实收件 {original.first_receipt.packet_id}，首执 {original.first_receipt.receipt_id}')


if __name__ == '__main__':
    main()
