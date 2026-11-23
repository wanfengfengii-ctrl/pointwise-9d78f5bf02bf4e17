"""Disposition bookkeeping: opening, revising and reissuing tickets.

A signed report remembers the frozen procedure, the accepted packet and the
fixed rational deviation of its specimen.  A disposition ticket names the
report actually executed at the sorting desk.  Opening requires evidence and a
matching rule; a category change is arbitrated by the sorting ledger before the
new report is signed, while a same-category correction keeps every unexpired
ticket alive.
"""
from dataclasses import dataclass
import json
import sqlite3

from exchange.catalog import get_accepted_packet
from procedure.guards import (
    format_rational,
    parse_rational,
    require_id,
    require_text,
)
from report.archive import insert_first_receipt, insert_report
from review.decision import DecisionStatus, decide
from review.request import ReviewRequest

RELEASE = 'release'
ISOLATE = 'isolate'

OPEN = 'open'
REVISE = 'revise'
PENDING = 'pending'
SIGNED = 'signed'
CONFLICT = 'conflict'


@dataclass(frozen=True)
class _Specimen:
    report_id: int
    category: str
    generation: int
    deviation: object


def _category(status: DecisionStatus) -> str:
    if status is DecisionStatus.PASS:
        return RELEASE
    if status is DecisionStatus.FAIL:
        return ISOLATE
    raise ValueError(f'a {status.value} decision cannot be issued as a disposition')


def _ticket(specimen: str, report_id: int, category: str, generation: int,
            packet_id: int, deviation) -> dict:
    return {
        'specimen': specimen,
        'report_id': report_id,
        'category': category,
        'generation': generation,
        'packet_id': packet_id,
        'deviation': format_rational(deviation),
    }


def _encode_ticket(ticket: dict) -> str:
    return json.dumps(ticket, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _load_ticket(ticket_json: str) -> dict:
    return json.loads(ticket_json)


def _request_row(archive: sqlite3.Connection, request_id: str):
    return archive.execute(
        '''SELECT kind, specimen, packet_id, parent_report_id, deviation,
                  category, generation, status, report_id, ticket_json
           FROM disposition_requests WHERE request_id = ?''',
        (request_id,),
    ).fetchone()


def _pending_for(archive: sqlite3.Connection, specimen: str):
    return archive.execute(
        '''SELECT request_id FROM disposition_requests
           WHERE specimen = ? AND status = ? ORDER BY rowid LIMIT 1''',
        (specimen, PENDING),
    ).fetchone()


def _specimen_row(archive: sqlite3.Connection, specimen: str) -> _Specimen | None:
    row = archive.execute(
        '''SELECT report_id, category, generation, deviation
           FROM disposition_specimens WHERE specimen = ?''',
        (specimen,),
    ).fetchone()
    if row is None:
        return None
    return _Specimen(row[0], row[1], row[2], parse_rational(row[3]))


def _decide(catalog: sqlite3.Connection, packet_id: int, deviation):
    packet = get_accepted_packet(catalog, packet_id)
    if packet is None:
        raise LookupError(f'unknown accepted packet {packet_id}')
    verdict = decide(packet, ReviewRequest(packet_id, deviation))
    if verdict.status not in (DecisionStatus.PASS, DecisionStatus.FAIL):
        raise ValueError(
            f'accepted packet {packet_id} cannot be dispositioned: {verdict.status.value}'
        )
    return verdict


def _sign(archive: sqlite3.Connection, specimen: str, report_id: int, category: str,
          generation: int, deviation) -> None:
    """Record the newly signed version inside an open signing transaction."""
    archive.execute(
        '''INSERT INTO disposition_specimens
               (specimen, report_id, category, generation, deviation)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (specimen) DO UPDATE SET
               report_id = excluded.report_id,
               category = excluded.category,
               generation = excluded.generation''',
        (specimen, report_id, category, generation, format_rational(deviation)),
    )


def _insert_request(archive: sqlite3.Connection, *, request_id: str, kind: str,
                    specimen: str, packet_id: int, parent_report_id: int | None,
                    deviation, category: str, generation: int, status: str,
                    report_id: int | None = None, ticket: dict | None = None) -> None:
    archive.execute(
        '''INSERT INTO disposition_requests
               (request_id, kind, specimen, packet_id, parent_report_id, deviation,
                category, generation, status, report_id, ticket_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (request_id, kind, specimen, packet_id, parent_report_id,
         format_rational(deviation), category, generation, status, report_id,
         None if ticket is None else _encode_ticket(ticket)),
    )


def _deliver(send, specimen: str, ticket: dict) -> None:
    if send is not None:
        send({'op': 'deliver', 'specimen': specimen, 'ticket': ticket})


def open_disposition(catalog: sqlite3.Connection, archive: sqlite3.Connection,
                     send, request_id: str, specimen: str, packet_id: int, deviation):
    """Open a specimen and sign its first disposition report."""
    request_id = require_text(request_id, 'disposition request')
    specimen = require_text(specimen, 'specimen')
    packet_id = require_id(packet_id, 'accepted packet')
    deviation = parse_rational(deviation)

    existing = _request_row(archive, request_id)
    if existing is not None:
        kind, owner, stored_packet, _, stored_deviation, _, _, status, report_id, ticket_json = existing
        if (kind != OPEN or owner != specimen or stored_packet != packet_id
                or parse_rational(stored_deviation) != deviation or status != SIGNED):
            raise ValueError('disposition request id reused with different parameters')
        ticket = None if ticket_json is None else _load_ticket(ticket_json)
        if ticket is not None:
            _deliver(send, specimen, ticket)
        return {'report_id': report_id, 'ticket': ticket}

    if _specimen_row(archive, specimen) is not None:
        raise ValueError(f'specimen {specimen!r} already has a disposition')
    if _pending_for(archive, specimen) is not None:
        raise ValueError(f'specimen {specimen!r} has a request awaiting arbitration')

    verdict = _decide(catalog, packet_id, deviation)
    category = _category(verdict.status)
    generation = 0

    if archive.in_transaction:
        raise RuntimeError('disposition opening requires an idle connection')
    with archive:
        archive.execute('BEGIN IMMEDIATE')
        report_id = insert_report(archive, verdict)
        insert_first_receipt(archive, report_id, verdict)
        ticket = _ticket(specimen, report_id, category, generation, packet_id, deviation)
        _sign(archive, specimen, report_id, category, generation, deviation)
        _insert_request(
            archive, request_id=request_id, kind=OPEN, specimen=specimen,
            packet_id=packet_id, parent_report_id=None, deviation=deviation,
            category=category, generation=generation, status=SIGNED,
            report_id=report_id, ticket=ticket,
        )

    _deliver(send, specimen, ticket)
    return {'report_id': report_id, 'ticket': ticket}


def revise_disposition(catalog: sqlite3.Connection, archive: sqlite3.Connection,
                       send, request_id: str, specimen: str, parent_report_id: int,
                       packet_id: int):
    """Correct a specimen's current report with another accepted packet."""
    request_id = require_text(request_id, 'disposition request')
    specimen = require_text(specimen, 'specimen')
    parent_report_id = require_id(parent_report_id, 'parent report')
    packet_id = require_id(packet_id, 'accepted packet')

    existing = _request_row(archive, request_id)
    if existing is not None:
        (kind, owner, stored_packet, stored_parent, stored_deviation, category,
         generation, status, report_id, ticket_json) = existing
        if (kind != REVISE or owner != specimen or stored_packet != packet_id
                or stored_parent != parent_report_id):
            raise ValueError('disposition request id reused with different parameters')
        if status == SIGNED:
            ticket = None if ticket_json is None else _load_ticket(ticket_json)
            if ticket is not None:
                _deliver(send, specimen, ticket)
            return {'status': SIGNED, 'report_id': report_id}
        if status == CONFLICT:
            return {'status': CONFLICT, 'report_id': report_id}
        pending = (category, generation, parse_rational(stored_deviation))
    else:
        current = _specimen_row(archive, specimen)
        if current is None:
            raise LookupError(f'unknown specimen {specimen!r}')
        if current.report_id != parent_report_id:
            raise ValueError('a revision must parent the current report')
        if _pending_for(archive, specimen) is not None:
            raise ValueError(f'specimen {specimen!r} has a request awaiting arbitration')
        verdict = _decide(catalog, packet_id, current.deviation)
        category = _category(verdict.status)
        generation = current.generation if category == current.category else current.generation + 1
        pending = (category, generation, current.deviation)
        if archive.in_transaction:
            raise RuntimeError('disposition revision requires an idle connection')
        with archive:
            archive.execute('BEGIN IMMEDIATE')
            _insert_request(
                archive, request_id=request_id, kind=REVISE, specimen=specimen,
                packet_id=packet_id, parent_report_id=parent_report_id,
                deviation=current.deviation, category=category, generation=generation,
                status=PENDING,
            )

    category, generation, deviation = pending
    current = _specimen_row(archive, specimen)
    if current is None:
        raise LookupError(f'unknown specimen {specimen!r}')

    if category != current.category:
        if send is None:
            raise ValueError('a category change requires a sorting desk')
        reply = send({
            'op': 'revise', 'request_id': request_id, 'specimen': specimen,
            'category': category, 'generation': generation,
        })
        if not isinstance(reply, dict) or reply.get('status') not in ('applied', 'conflict'):
            raise ValueError('sorting desk returned an invalid revision reply')
        if reply['status'] == CONFLICT:
            if archive.in_transaction:
                raise RuntimeError('disposition revision requires an idle connection')
            with archive:
                archive.execute('BEGIN IMMEDIATE')
                archive.execute(
                    'UPDATE disposition_requests SET status = ?, report_id = ? WHERE request_id = ?',
                    (CONFLICT, parent_report_id, request_id),
                )
            return {'status': CONFLICT, 'report_id': parent_report_id}

    verdict = _decide(catalog, packet_id, deviation)

    if archive.in_transaction:
        raise RuntimeError('disposition revision requires an idle connection')
    with archive:
        archive.execute('BEGIN IMMEDIATE')
        report_id = insert_report(archive, verdict)
        insert_first_receipt(archive, report_id, verdict)
        ticket = _ticket(specimen, report_id, category, generation, packet_id, deviation)
        _sign(archive, specimen, report_id, category, generation, deviation)
        archive.execute(
            '''UPDATE disposition_requests
               SET status = ?, report_id = ?, ticket_json = ? WHERE request_id = ?''',
            (SIGNED, report_id, _encode_ticket(ticket), request_id),
        )

    _deliver(send, specimen, ticket)
    return {'status': SIGNED, 'report_id': report_id}


def issue_disposition(archive: sqlite3.Connection, send, specimen: str):
    """Deliver the current report's ticket for a specimen.

    While a request awaits arbitration no new version can be signed, but the
    already signed current version stays issuable and retrievable as usual.
    """
    specimen = require_text(specimen, 'specimen')
    current = _specimen_row(archive, specimen)
    if current is None:
        raise LookupError(f'unknown specimen {specimen!r}')
    row = archive.execute(
        '''SELECT ticket_json FROM disposition_requests
           WHERE report_id = ? AND ticket_json IS NOT NULL
           ORDER BY rowid DESC LIMIT 1''',
        (current.report_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f'specimen {specimen!r} has no issued ticket')
    ticket = _load_ticket(row[0])
    _deliver(send, specimen, ticket)
    return ticket


def get_disposition(archive: sqlite3.Connection, specimen: str):
    """Read the current report number and any pending request."""
    specimen = require_text(specimen, 'specimen')
    current = _specimen_row(archive, specimen)
    pending = _pending_for(archive, specimen)
    if current is None and pending is None:
        return None
    return {
        'report_id': None if current is None else current.report_id,
        'pending': None if pending is None else pending[0],
    }
