"""Sorting desk ledger for executed disposition tickets.

The desk keeps an independent SQLite database.  It receives tickets from the
report archive over the ``exchange`` port, records at most one execution per
specimen, and owns the ordering between a category change and a late ticket
execution.

A ticket is ``{"specimen", "report_id", "category", "generation"}`` (extra
JSON-serializable fields may accompany it).  ``generation`` advances whenever
the archive commits a category change for that specimen, so every ticket from
an earlier generation is void from the sorting commit onward and stays void
even if the category later returns to its original value.
"""
from dataclasses import dataclass
import json
import sqlite3

from procedure.guards import require_id, require_text


RELEASE = 'release'
ISOLATE = 'isolate'
CATEGORIES = (RELEASE, ISOLATE)


@dataclass(frozen=True)
class _Ticket:
    specimen: str
    report_id: int
    category: str
    generation: int
    payload: str


def _generation(value: object) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError('a ticket generation must be a nonnegative integer')
    return value


def _ticket(ticket: object) -> _Ticket:
    if not isinstance(ticket, dict):
        raise ValueError('a ticket must be a JSON object')
    specimen = require_text(ticket.get('specimen'), 'ticket specimen')
    report_id = require_id(ticket.get('report_id'), 'ticket report')
    category = ticket.get('category')
    if category not in CATEGORIES:
        raise ValueError('a ticket category must be release or isolate')
    generation = _generation(ticket.get('generation'))
    document = dict(ticket)
    document['generation'] = generation
    try:
        payload = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError) as error:
        raise ValueError('a ticket must be JSON serializable') from error
    return _Ticket(specimen, report_id, category, generation, payload)


def _execution(request_id: str, payload: str) -> dict:
    return {'request_id': request_id, 'ticket': json.loads(payload)}


class SortingDesk:
    """Execute disposition tickets and remember the actual first execution."""

    def __init__(self, db: sqlite3.Connection, *, after_commit=None):
        if db.in_transaction:
            raise RuntimeError('sorting initialization requires an idle connection')
        if after_commit is not None and not callable(after_commit):
            raise ValueError('after_commit must be callable')
        self.db = db
        self.after_commit = after_commit
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''
                CREATE TABLE IF NOT EXISTS sorting_specimens (
                    specimen TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    generation INTEGER NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS sorting_tickets (
                    report_id INTEGER PRIMARY KEY,
                    specimen TEXT NOT NULL,
                    category TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    ticket_json TEXT NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS sorting_executions (
                    specimen TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    report_id INTEGER NOT NULL,
                    ticket_json TEXT NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS sorting_applies (
                    request_id TEXT PRIMARY KEY,
                    specimen TEXT NOT NULL,
                    report_id INTEGER NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS sorting_revisions (
                    request_id TEXT PRIMARY KEY,
                    specimen TEXT NOT NULL,
                    category TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
            ''')

    # -- delivery ---------------------------------------------------------

    def _register(self, specimen: str, raw_ticket: object) -> None:
        """Store a delivered ticket; delivery may be late or repeated."""
        ticket = _ticket(raw_ticket)
        if ticket.specimen != specimen:
            raise ValueError('a ticket may not be delivered to another specimen')
        row = self.db.execute(
            'SELECT specimen, ticket_json FROM sorting_tickets WHERE report_id = ?',
            (ticket.report_id,),
        ).fetchone()
        if row is not None:
            if row[0] != specimen or row[1] != ticket.payload:
                raise ValueError('a report ticket changed on redelivery')
            return
        self.db.execute(
            '''INSERT OR IGNORE INTO sorting_specimens (specimen, category, generation)
               VALUES (?, ?, ?)''',
            (specimen, ticket.category, ticket.generation),
        )
        self.db.execute(
            '''INSERT INTO sorting_tickets
                   (report_id, specimen, category, generation, ticket_json)
               VALUES (?, ?, ?, ?, ?)''',
            (ticket.report_id, specimen, ticket.category, ticket.generation, ticket.payload),
        )

    # -- execution --------------------------------------------------------

    def apply(self, request_id: str, specimen: str, ticket: object):
        """Execute an eligible ticket; the first execution for a specimen wins."""
        request_id = require_text(request_id, 'execution request')
        specimen = require_text(specimen, 'specimen')
        wanted = _ticket(ticket)
        if wanted.specimen != specimen:
            raise ValueError('a ticket may not be executed for another specimen')
        if self.db.in_transaction:
            raise RuntimeError('ticket execution requires an idle connection')

        first = False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            applied = self.db.execute(
                'SELECT specimen, report_id FROM sorting_applies WHERE request_id = ?',
                (request_id,),
            ).fetchone()
            if applied is not None and (applied[0] != specimen or applied[1] != wanted.report_id):
                raise ValueError('execution request id reused with different parameters')
            if applied is not None:
                executed = self.db.execute(
                    'SELECT request_id, ticket_json FROM sorting_executions WHERE specimen = ?',
                    (specimen,),
                ).fetchone()
                if executed is not None:
                    if executed[1] != wanted.payload:
                        raise ValueError('execution request id reused with different parameters')
                    return _execution(executed[0], executed[1])

            # Delivery may be late; an execution also accepts the original
            # ticket, while a different payload for the same report is refused.
            self._register(specimen, ticket)
            state = self.db.execute(
                'SELECT category, generation FROM sorting_specimens WHERE specimen = ?',
                (specimen,),
            ).fetchone()
            stored = self.db.execute(
                'SELECT ticket_json FROM sorting_tickets WHERE report_id = ?',
                (wanted.report_id,),
            ).fetchone()
            if state is None or stored is None:
                return None
            if stored[0] != wanted.payload:
                raise ValueError('the supplied ticket differs from the delivered ticket')
            if state[0] != wanted.category or state[1] != wanted.generation:
                # A category change voided this ticket, however late it arrives.
                return None

            executed = self.db.execute(
                'SELECT request_id, ticket_json FROM sorting_executions WHERE specimen = ?',
                (specimen,),
            ).fetchone()
            if executed is not None:
                return _execution(executed[0], executed[1])

            payload = stored[0]
            self.db.execute(
                '''INSERT INTO sorting_executions (specimen, request_id, report_id, ticket_json)
                   VALUES (?, ?, ?, ?)''',
                (specimen, request_id, wanted.report_id, payload),
            )
            self.db.execute(
                '''INSERT INTO sorting_applies (request_id, specimen, report_id)
                   VALUES (?, ?, ?)''',
                (request_id, specimen, wanted.report_id),
            )
            result = _execution(request_id, payload)
            first = True

        if first and self.after_commit is not None:
            self.after_commit('apply', request_id)
        return result

    def get_execution(self, specimen: str):
        specimen = require_text(specimen, 'specimen')
        row = self.db.execute(
            'SELECT request_id, ticket_json FROM sorting_executions WHERE specimen = ?',
            (specimen,),
        ).fetchone()
        if row is None:
            return None
        return _execution(row[0], row[1])

    # -- archive port -----------------------------------------------------

    def exchange(self, message: object):
        """Accept a JSON-serializable record from the report archive."""
        if not isinstance(message, dict):
            raise ValueError('sorting exchange requires a JSON object')
        operation = message.get('op')
        if operation == 'deliver':
            specimen = require_text(message.get('specimen'), 'specimen')
            if self.db.in_transaction:
                raise RuntimeError('ticket delivery requires an idle connection')
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self._register(specimen, message.get('ticket'))
            return {'status': 'stored'}
        if operation == 'revise':
            return self._revise(message)
        raise ValueError('unknown sorting exchange operation')

    def _revise(self, message: dict):
        request_id = require_text(message.get('request_id'), 'revision request')
        specimen = require_text(message.get('specimen'), 'specimen')
        category = message.get('category')
        if category not in CATEGORIES:
            raise ValueError('a revision category must be release or isolate')
        generation = message.get('generation')
        if type(generation) is not int or generation < 0:
            raise ValueError('a revision generation must be a nonnegative integer')
        if self.db.in_transaction:
            raise RuntimeError('category revision requires an idle connection')

        first = False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            stored = self.db.execute(
                '''SELECT specimen, category, generation, status
                   FROM sorting_revisions WHERE request_id = ?''',
                (request_id,),
            ).fetchone()
            if stored is not None:
                if stored[:3] != (specimen, category, generation):
                    raise ValueError('revision request id reused with different parameters')
                status = stored[3]
            else:
                status = self._rule(specimen, category, generation)
                self.db.execute(
                    '''INSERT INTO sorting_revisions
                           (request_id, specimen, category, generation, status)
                       VALUES (?, ?, ?, ?, ?)''',
                    (request_id, specimen, category, generation, status),
                )
                first = True

        if first and self.after_commit is not None:
            self.after_commit('revise', request_id)
        return {'status': status}

    def _rule(self, specimen: str, category: str, generation: int) -> str:
        """Apply a category change to the ledger, returning applied or conflict."""
        state = self.db.execute(
            'SELECT category, generation FROM sorting_specimens WHERE specimen = ?',
            (specimen,),
        ).fetchone()
        executed = self.db.execute(
            'SELECT 1 FROM sorting_executions WHERE specimen = ?', (specimen,)
        ).fetchone()
        if executed is not None:
            if state is not None and state[0] == category:
                return 'applied'
            return 'conflict'
        if state is None:
            self.db.execute(
                'INSERT INTO sorting_specimens (specimen, category, generation) VALUES (?, ?, ?)',
                (specimen, category, generation),
            )
            return 'applied'
        current_category, current_generation = state
        if category == current_category and generation == current_generation:
            return 'applied'
        if generation <= current_generation:
            raise ValueError('a category revision must advance the sorting generation')
        self.db.execute(
            'UPDATE sorting_specimens SET category = ?, generation = ? WHERE specimen = ?',
            (category, generation, specimen),
        )
        return 'applied'
