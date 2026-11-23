import sqlite3

from report.archive import read_signed_snapshot
from report.reconcile import ReconciliationView, reconcile_provenance


def get_report(archive: sqlite3.Connection, report_id: int) -> ReconciliationView | None:
    snapshot = read_signed_snapshot(archive, report_id)
    return None if snapshot is None else reconcile_provenance(snapshot)
