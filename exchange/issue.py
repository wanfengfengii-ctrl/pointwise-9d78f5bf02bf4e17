import sqlite3

from exchange.body import V1Body, V1Rule, encode_body
from exchange.envelope import V1Envelope, seal_v1
from procedure.conversion import bind_conversion
from procedure.freeze import FrozenRule, load_frozen_procedure


def _public_rule(rule: FrozenRule) -> V1Rule:
    evidence = rule.evidence
    if evidence is not None and evidence.normalized is None:
        return V1Rule(rule.rule_id, rule.interval, None)
    reference = None if evidence is None else evidence.normalized
    return V1Rule(rule.rule_id, rule.interval, bind_conversion(rule.conversion, reference))


def issue_packet(source: sqlite3.Connection, procedure_id: int) -> V1Envelope:
    frozen = load_frozen_procedure(source, procedure_id)
    body = V1Body(frozen.procedure_id, tuple(_public_rule(rule) for rule in frozen.rules))
    return seal_v1(encode_body(body))
