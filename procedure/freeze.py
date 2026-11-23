from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import sqlite3

from procedure.conversion import ConversionSpec, validate_conversion
from procedure.edit import ProcedureDraft, validate_draft
from procedure.evidence import FrozenEvidence, resolve_evidence
from procedure.guards import (
    RationalInterval,
    format_rational,
    parse_rational,
    require_id,
    validate_interval,
)


@dataclass(frozen=True)
class FrozenRule:
    rule_id: str
    interval: RationalInterval
    conversion: ConversionSpec
    evidence: FrozenEvidence | None


@dataclass(frozen=True)
class ProcedureRevision:
    procedure_id: int
    rules: tuple[FrozenRule, ...]


def initialize_procedure_store(source: sqlite3.Connection) -> None:
    if source.in_transaction:
        raise RuntimeError('procedure initialization requires an idle connection')
    source.execute('''
        CREATE TABLE IF NOT EXISTS procedure_revisions (
            procedure_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rules_json TEXT NOT NULL
        )
    ''')


def _encode_fraction(value: object) -> str:
    if isinstance(value, Fraction):
        return format_rational(value)
    raise TypeError(f'unsupported frozen value {type(value).__name__}')


def _restore_rule(document: dict) -> FrozenRule:
    evidence_data = document['evidence']
    evidence = None
    if evidence_data is not None:
        normalized = evidence_data['normalized']
        evidence = FrozenEvidence(
            reference_name=evidence_data['reference_name'],
            revision_id=evidence_data['revision_id'],
            normalized=None if normalized is None else parse_rational(normalized),
            provenance=evidence_data['provenance'],
        )
    return FrozenRule(
        document['rule_id'],
        validate_interval(RationalInterval(**document['interval'])),
        validate_conversion(ConversionSpec(**document['conversion'])),
        evidence,
    )


def freeze_procedure(source: sqlite3.Connection, draft: ProcedureDraft) -> ProcedureRevision:
    checked = validate_draft(draft)
    if source.in_transaction:
        raise RuntimeError('procedure freeze requires an idle connection')
    with source:
        # All lookups and the new frozen row see one source state.
        source.execute('BEGIN IMMEDIATE')
        rules = tuple(
            FrozenRule(rule.rule_id, rule.interval, rule.conversion, resolve_evidence(source, rule))
            for rule in checked.rules
        )
        serialized = json.dumps([asdict(rule) for rule in rules], default=_encode_fraction)
        cursor = source.execute(
            'INSERT INTO procedure_revisions (rules_json) VALUES (?)', (serialized,)
        )
        procedure_id = cursor.lastrowid
    return ProcedureRevision(procedure_id, rules)


def load_frozen_procedure(source: sqlite3.Connection, procedure_id: int) -> ProcedureRevision:
    require_id(procedure_id, 'frozen procedure')
    row = source.execute(
        'SELECT rules_json FROM procedure_revisions WHERE procedure_id = ?', (procedure_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f'unknown frozen procedure {procedure_id}')
    rules = tuple(_restore_rule(rule) for rule in json.loads(row[0]))
    return ProcedureRevision(procedure_id, rules)
