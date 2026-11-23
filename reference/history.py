import sqlite3

from procedure.guards import parse_rational, require_id, require_text
from reference.reading import ReferenceReading
from reference.revisions import ReferenceRevision


_READ_REVISION = '''
    SELECT revision_id, name, raw_value, unit, provenance, normalized
    FROM reference_revisions WHERE name = ?
'''


def _revision(row: tuple | None) -> ReferenceRevision | None:
    if row is None:
        return None
    revision_id, name, raw_value, unit, provenance, normalized = row
    reading = ReferenceReading(name, parse_rational(raw_value), unit, provenance)
    return ReferenceRevision(revision_id, reading, parse_rational(normalized))


def get_current_revision(source: sqlite3.Connection, name: str) -> ReferenceRevision | None:
    require_text(name, 'reference name')
    row = source.execute(
        _READ_REVISION + ' ORDER BY revision_id DESC LIMIT 1', (name,)
    ).fetchone()
    return _revision(row)


def get_reference_revision(
    source: sqlite3.Connection, name: str, revision_id: int
) -> ReferenceRevision | None:
    require_text(name, 'reference name')
    require_id(revision_id, 'reference revision')
    row = source.execute(
        _READ_REVISION + ' AND revision_id = ?', (name, revision_id)
    ).fetchone()
    return _revision(row)
