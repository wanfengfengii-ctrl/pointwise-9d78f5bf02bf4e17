from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from exchange.catalog import AcceptedPacket
from procedure.guards import matches_interval
from review.measurement import compute_measurement, meets_quality_limit
from review.request import ReviewRequest


class DecisionStatus(StrEnum):
    PASS = 'pass'
    FAIL = 'fail'
    MISSING_EVIDENCE = 'missing_evidence'
    NO_RULE = 'no_rule'


@dataclass(frozen=True)
class Decision:
    packet_id: int
    procedure_id: int
    deviation: Fraction
    status: DecisionStatus
    rule_id: str | None
    value: Fraction | None


def decide(packet: AcceptedPacket, request: ReviewRequest) -> Decision:
    if packet.packet_id != request.packet_id:
        raise ValueError('review request does not match the supplied packet')
    parent = (packet.packet_id, packet.procedure_id, request.deviation)
    for rule in packet.body.rules:
        if not matches_interval(rule.interval, request.deviation):
            continue
        if rule.conversion is None:
            return Decision(*parent, DecisionStatus.MISSING_EVIDENCE, rule.rule_id, None)
        value = compute_measurement(rule.conversion, request.deviation)
        status = DecisionStatus.PASS if meets_quality_limit(rule.conversion, value) else DecisionStatus.FAIL
        return Decision(*parent, status, rule.rule_id, value)
    return Decision(*parent, DecisionStatus.NO_RULE, None, None)
