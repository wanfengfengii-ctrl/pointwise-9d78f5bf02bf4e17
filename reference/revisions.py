from dataclasses import dataclass
from fractions import Fraction
import sqlite3

from procedure.guards import format_rational, require_id
from reference.reading import ReferenceReading


@dataclass(frozen=True)
class ReferenceRevision:
    revision_id: int
    reading: ReferenceReading
    normalized: Fraction


def initialize_reference_store(source: sqlite3.Connection) -> None:
    if source.in_transaction:
        raise RuntimeError('reference initialization requires an idle connection')
    with source:
        source.execute('BEGIN IMMEDIATE')
        source.execute('''
            CREATE TABLE IF NOT EXISTS reference_revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                raw_value TEXT NOT NULL,
                unit TEXT NOT NULL,
                provenance TEXT NOT NULL,
                normalized TEXT NOT NULL
            )
        ''')
        source.execute('''
            CREATE INDEX IF NOT EXISTS reference_name_history
            ON reference_revisions (name, revision_id DESC)
        ''')


def append_revision(
    source: sqlite3.Connection,
    reading: ReferenceReading,
    normalized: Fraction,
    *,
    replaces: int | None,
) -> ReferenceRevision:
    if replaces is not None:
        require_id(replaces, 'replaced revision')
    if source.in_transaction:
        raise RuntimeError('reference registration requires an idle connection')

    with source:
        source.execute('BEGIN IMMEDIATE')
        current = source.execute(
            '''SELECT revision_id FROM reference_revisions
               WHERE name = ? ORDER BY revision_id DESC LIMIT 1''',
            (reading.name,),
        ).fetchone()
        current_id = None if current is None else current[0]
        if replaces != current_id:
            raise ValueError('registration must name the current revision when correcting a reference')
        cursor = source.execute(
            '''INSERT INTO reference_revisions (name, raw_value, unit, provenance, normalized)
               VALUES (?, ?, ?, ?, ?)''',
            (
                reading.name,
                format_rational(reading.raw_value),
                reading.unit,
                reading.provenance,
                format_rational(normalized),
            ),
        )
        revision_id = cursor.lastrowid
    return ReferenceRevision(revision_id, reading, normalized)
