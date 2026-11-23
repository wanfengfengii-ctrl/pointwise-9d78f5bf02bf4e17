"""Single verification entry: original regression plus disposition checks."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import traceback

import check_project
from disposition_fixture import DEVIATION, EXAMPLES, workbench
from disposition_scenarios import late_ticket_scenario, pending_revision_scenario
from report.history import get_report


def _fixture_checks():
    """Verify the four frozen V1 verdicts and re-open their original reports."""
    with TemporaryDirectory(prefix='jin-heng-verify-fixture-') as directory:
        root = Path(directory)
        reports = {}
        with workbench(root) as (catalog, archive, sorting, packets):
            for name, (_, status, value) in EXAMPLES.items():
                packet = packets[name]
                from review.service import review_case
                view = review_case(catalog, archive, packet.packet_id, DEVIATION)
                actual = (view.decision.status.value, view.decision.value)
                assert actual == (status, value), (name, actual)
                assert view.provenance.rule_id == name, (name, view.provenance)
                reports[name] = (view.report_id, view)
            assert archive.execute('PRAGMA database_list').fetchone()[2] != (
                sorting.db.execute('PRAGMA database_list').fetchone()[2]
            )
        with closing(sqlite3.connect(root / 'reports.db')) as reopened:
            for name, (report_id, before) in reports.items():
                assert get_report(reopened, report_id) == before, name
        return {name: report_id for name, (report_id, _) in reports.items()}


def _late_ticket_checks():
    with TemporaryDirectory(prefix='jin-heng-verify-late-') as directory:
        outcome = late_ticket_scenario(Path(directory))
    opened, upgraded = outcome['opened'], outcome['upgraded']
    execution = outcome['execution']
    assert execution['ticket']['report_id'] == opened['report_id']
    assert outcome['conflict'] == {'status': 'conflict', 'report_id': upgraded['report_id']}
    assert outcome['corrected']['status'] == 'signed'
    return outcome


def _pending_revision_checks():
    with TemporaryDirectory(prefix='jin-heng-verify-pending-') as directory:
        outcome = pending_revision_scenario(Path(directory))
    assert outcome['signed']['status'] == 'signed'
    assert outcome['final']['status'] == 'signed'
    assert outcome['execution']['ticket']['report_id'] == outcome['final']['report_id']
    assert all(report is not None for report in outcome['reports'])
    return outcome


def main():
    steps = (
        ('regression', lambda: check_project.main() == 0),
        ('fixture', lambda: _fixture_checks() is not None),
        ('late_ticket', lambda: _late_ticket_checks() is not None),
        ('pending_revision', lambda: _pending_revision_checks() is not None),
    )
    failures = []
    for name, check in steps:
        try:
            if not check():
                failures.append((name, 'check returned false'))
        except BaseException:
            failures.append((name, traceback.format_exc()))
    for name, detail in failures:
        print(f'VERIFY FAIL {name}\n{detail}', file=sys.stderr)
    print(f'DISPOSITION VERIFY {"PASS" if not failures else "FAIL"} '
          f'steps={len(steps)} failures={len(failures)}')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
