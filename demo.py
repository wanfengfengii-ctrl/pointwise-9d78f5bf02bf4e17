from contextlib import closing
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory

from disposition_fixture import DropReply, prepare_examples, DEVIATION
from exchange.accept import receive_packet
from exchange.catalog import initialize_catalog
from exchange.issue import issue_packet
from procedure.conversion import ConversionSpec
from procedure.edit import DraftRule, ProcedureDraft
from procedure.freeze import freeze_procedure, initialize_procedure_store
from procedure.guards import RationalInterval, format_rational
from reference.ingest import record_reference
from reference.revisions import initialize_reference_store
from report.archive import initialize_archive, initialize_disposition_archive
from report.history import get_report
from review.service import (
    get_disposition,
    issue_disposition,
    open_disposition,
    revise_disposition,
    review_case,
)
from sorting import SortingDesk


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


def _print_disposition(outcome):
    opened, upgraded = outcome['opened'], outcome['upgraded']
    execution = outcome['execution']
    corrected = outcome['corrected']
    print('DISPOSITION_SCENARIO ' + json.dumps({
        'case_id': 'late-ticket-after-upgrade',
        'opened_report': opened['report_id'],
        'upgraded_report': upgraded['report_id'],
        'executed_report': execution['ticket']['report_id'],
        'executed_request': execution['request_id'],
        'conflict_report': outcome['conflict']['report_id'],
        'corrected_report': corrected['report_id'],
        'oracle': '开 R6 后换 R8，迟到 R6 原单实记；改 I12 拒签并保留 R8，同类改 R4 可签。',
    }, ensure_ascii=False, sort_keys=True))


def disposition_demo(root):
    """Shut down the source and show late-ticket/retrieval disposition results."""
    catalog = sqlite3.connect(root / 'intake.db')
    archive = sqlite3.connect(root / 'reports.db')
    sorting_db = sqlite3.connect(root / 'sorting.db')
    initialize_catalog(catalog)
    initialize_archive(archive)
    initialize_disposition_archive(archive)
    desk = SortingDesk(sorting_db)
    with closing(sqlite3.connect(root / 'source.db')) as source:
        packets = prepare_examples(source, catalog)
    try:
        send = desk.exchange
        opened = open_disposition(catalog, archive, send, 'demo-open', 'A',
                                  packets['R6'].packet_id, DEVIATION)
        upgraded = revise_disposition(catalog, archive, send, 'demo-r8', 'A',
                                      opened['report_id'], packets['R8'].packet_id)
        desk.after_commit = DropReply('apply', 'demo-exec')
        try:
            desk.apply('demo-exec', 'A', opened['ticket'])
        except TimeoutError:
            pass
        desk.after_commit = None
        if sorting_db is not None:
            sorting_db.close()
        sorting_db = sqlite3.connect(root / 'sorting.db')
        desk = SortingDesk(sorting_db)
        send = desk.exchange
        execution = desk.get_execution('A')
        conflict = revise_disposition(catalog, archive, send, 'demo-i12', 'A',
                                      upgraded['report_id'], packets['I12'].packet_id)
        corrected = revise_disposition(catalog, archive, send, 'demo-r4', 'A',
                                       upgraded['report_id'], packets['R4'].packet_id)
        corrected_ticket = issue_disposition(archive, send, 'A')
        assert execution['ticket']['report_id'] == opened['report_id']
        assert conflict == {'status': 'conflict', 'report_id': upgraded['report_id']}
        assert corrected['status'] == 'signed'
        history = []
        for stage in ('opened', 'upgraded', 'corrected'):
            report_id = {'opened': opened['report_id'], 'upgraded': upgraded['report_id'],
                         'corrected': corrected['report_id']}[stage]
            view = get_report(archive, report_id)
            assert view is not None
            history.append({'stage': stage, 'report_id': report_id,
                            'category': 'release' if view.decision.status.value == 'pass' else 'isolate',
                            'packet': view.first_receipt.packet_id,
                            'receipt': view.first_receipt.receipt_id})
        _print_disposition({
            'opened': opened, 'upgraded': upgraded, 'execution': execution,
            'conflict': conflict, 'corrected': corrected,
        })
        print('DISPOSITION_HISTORY ' + json.dumps(history, ensure_ascii=False, sort_keys=True))
        print(f'实际执行单 {execution["ticket"]["report_id"]}（请求 {execution["request_id"]}）'
              f'；现行报告 {get_disposition(archive, "A")["report_id"]}；'
              f'新单 {corrected_ticket["report_id"]}')
    finally:
        catalog.close()
        archive.close()
        sorting_db.close()


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
        disposition_demo(root)


if __name__ == '__main__':
    main()
