from dataclasses import dataclass
from fractions import Fraction
import sqlite3

from procedure.edit import DraftRule
from reference.history import get_current_revision, get_reference_revision


@dataclass(frozen=True)
class FrozenEvidence:
    reference_name: str
    revision_id: int | None
    normalized: Fraction | None
    provenance: str | None


def resolve_evidence(source: sqlite3.Connection, rule: DraftRule) -> FrozenEvidence | None:
    name = rule.reference_name
    if name is None:
        return None
    if rule.reference_revision is not None:
        selected = get_reference_revision(source, name, rule.reference_revision)
        if selected is None:
            raise ValueError(f'unknown revision {rule.reference_revision} for reference {name!r}')
    else:
        selected = get_current_revision(source, name)
        if selected is None:
            if rule.allow_missing:
                return FrozenEvidence(name, None, None, None)
            raise ValueError(f'required reference {name!r} is missing')
    return FrozenEvidence(name, selected.revision_id, selected.normalized, selected.reading.provenance)
