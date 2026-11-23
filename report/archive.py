from dataclasses import dataclass
import sqlite3

from procedure.guards import format_rational, parse_rational, require_id
from report.receipt import FirstReceipt
from review.decision import Decision, DecisionStatus


@dataclass(frozen=True)
class SignedReportSnapshot:
    report_id: int
    decision: Decision
    first_receipt: FirstReceipt


def initialize_archive(archive: sqlite3.Connection) -> None:
    if archive.in_transaction:
        raise RuntimeError('archive initialization requires an idle connection')
    archive.execute('PRAGMA foreign_keys = ON')
    with archive:
        archive.execute('BEGIN IMMEDIATE')
        archive.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                packet_id INTEGER NOT NULL,
                procedure_id INTEGER NOT NULL,
                deviation TEXT NOT NULL,
                status TEXT NOT NULL,
                rule_id TEXT,
                converted_value TEXT,
                CHECK (
                    (status IN ('pass', 'fail') AND rule_id IS NOT NULL AND converted_value IS NOT NULL)
                    OR (status = 'missing_evidence' AND rule_id IS NOT NULL AND converted_value IS NULL)
                    OR (status = 'no_rule' AND rule_id IS NULL AND converted_value IS NULL)
                )
            )
        ''')
        archive.execute('''
            CREATE TABLE IF NOT EXISTS first_receipts (
                receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL UNIQUE REFERENCES reports (report_id),
                packet_id INTEGER NOT NULL,
                procedure_id INTEGER NOT NULL
            )
        ''')


def initialize_disposition_archive(archive: sqlite3.Connection) -> None:
    """Enable disposition bookkeeping beside the signed report archive."""
    if archive.in_transaction:
        raise RuntimeError('disposition initialization requires an idle connection')
    with archive:
        archive.execute('BEGIN IMMEDIATE')
        archive.execute('''
            CREATE TABLE IF NOT EXISTS disposition_specimens (
                specimen TEXT PRIMARY KEY,
                report_id INTEGER NOT NULL REFERENCES reports (report_id),
                category TEXT NOT NULL,
                generation INTEGER NOT NULL,
                deviation TEXT NOT NULL
            )
        ''')
        archive.execute('''
            CREATE TABLE IF NOT EXISTS disposition_requests (
                request_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                specimen TEXT NOT NULL,
                packet_id INTEGER NOT NULL,
                parent_report_id INTEGER,
                deviation TEXT NOT NULL,
                category TEXT,
                generation INTEGER,
                status TEXT NOT NULL,
                report_id INTEGER,
                ticket_json TEXT
            )
        ''')


def insert_report(archive: sqlite3.Connection, decision: Decision) -> int:
    if not archive.in_transaction:
        raise RuntimeError('report insert requires a signing transaction')
    value = None if decision.value is None else format_rational(decision.value)
    cursor = archive.execute(
        '''INSERT INTO reports (packet_id, procedure_id, deviation, status, rule_id, converted_value)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (
            decision.packet_id,
            decision.procedure_id,
            format_rational(decision.deviation),
            decision.status.value,
            decision.rule_id,
            value,
        ),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError('signing did not insert a report')
    return cursor.lastrowid


def insert_first_receipt(archive: sqlite3.Connection, report_id: int, decision: Decision) -> int:
    if not archive.in_transaction:
        raise RuntimeError('receipt insert requires a signing transaction')
    cursor = archive.execute(
        '''INSERT INTO first_receipts (report_id, packet_id, procedure_id)
           VALUES (?, ?, ?)''',
        (report_id, decision.packet_id, decision.procedure_id),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError('signing did not insert a first receipt')
    return cursor.lastrowid


def read_signed_snapshot(archive: sqlite3.Connection, report_id: int) -> SignedReportSnapshot | None:
    require_id(report_id, 'report')
    row = archive.execute(
        '''SELECT r.report_id, r.packet_id, r.procedure_id, r.deviation, r.status,
                  r.rule_id, r.converted_value, f.receipt_id, f.report_id, f.packet_id, f.procedure_id
           FROM reports AS r LEFT JOIN first_receipts AS f ON f.report_id = r.report_id
           WHERE r.report_id = ?''',
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    stored_id, packet_id, procedure_id, deviation, status, rule_id, value = row[:7]
    receipt_id, receipt_report, receipt_packet, receipt_procedure = row[7:]
    if receipt_id is None:
        raise sqlite3.DatabaseError(f'signed report {report_id} has no first receipt')
    decision = Decision(
        packet_id,
        procedure_id,
        parse_rational(deviation),
        DecisionStatus(status),
        rule_id,
        None if value is None else parse_rational(value),
    )
    return SignedReportSnapshot(
        stored_id, decision, FirstReceipt(receipt_id, receipt_report, receipt_packet, receipt_procedure)
    )
