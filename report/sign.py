import sqlite3

from report.archive import SignedReportSnapshot, insert_first_receipt, insert_report
from report.receipt import make_first_receipt
from review.decision import Decision


def sign_report(archive: sqlite3.Connection, decision: Decision) -> SignedReportSnapshot:
    if archive.in_transaction:
        raise RuntimeError('report signing requires an idle connection')
    with archive:
        archive.execute('BEGIN IMMEDIATE')
        report_id = insert_report(archive, decision)
        receipt_id = insert_first_receipt(archive, report_id, decision)
        receipt = make_first_receipt(receipt_id, report_id, decision.packet_id, decision.procedure_id)
    return SignedReportSnapshot(report_id, decision, receipt)
