import sqlite3

from procedure.guards import RationalInput
from reference.reading import parse_reference_reading
from reference.revisions import ReferenceRevision, append_revision
from reference.units import normalize_reading


def record_reference(
    source: sqlite3.Connection,
    name: str,
    reading: RationalInput,
    unit: str,
    provenance: str,
    *,
    replaces: int | None = None,
) -> ReferenceRevision:
    original = parse_reference_reading(name, reading, unit, provenance)
    normalized = normalize_reading(original.raw_value, original.unit)
    return append_revision(source, original, normalized, replaces=replaces)
