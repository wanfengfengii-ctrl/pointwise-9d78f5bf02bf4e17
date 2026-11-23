from dataclasses import dataclass, replace

from procedure.conversion import ConversionSpec, validate_conversion
from procedure.guards import RationalInterval, require_id, require_text, validate_interval


@dataclass(frozen=True)
class DraftRule:
    rule_id: str
    interval: RationalInterval
    conversion: ConversionSpec
    reference_name: str | None = None
    reference_revision: int | None = None
    allow_missing: bool = False


@dataclass(frozen=True)
class ProcedureDraft:
    rules: tuple[DraftRule, ...] | list[DraftRule]


def validate_draft(draft: ProcedureDraft) -> ProcedureDraft:
    if not isinstance(draft, ProcedureDraft) or not isinstance(draft.rules, (tuple, list)):
        raise ValueError('expected an ordered rule draft')
    if not draft.rules:
        raise ValueError('a program needs at least one rule')
    seen = set()
    validated = []
    for rule in draft.rules:
        if not isinstance(rule, DraftRule):
            raise ValueError('expected a draft rule')
        require_text(rule.rule_id, 'rule id')
        if rule.rule_id in seen:
            raise ValueError(f'duplicate rule id {rule.rule_id!r}')
        seen.add(rule.rule_id)
        interval = validate_interval(rule.interval)
        conversion = validate_conversion(rule.conversion)
        if type(rule.allow_missing) is not bool:
            raise ValueError('missing-reference permission must be a boolean')
        if rule.reference_name is None:
            if rule.reference_revision is not None or rule.allow_missing or conversion.reference_factor != 0:
                raise ValueError(f'rule {rule.rule_id!r} must name its reference')
        else:
            require_text(rule.reference_name, 'reference name')
            if rule.reference_revision is not None:
                require_id(rule.reference_revision, 'selected reference revision')
        validated.append(replace(rule, interval=interval, conversion=conversion))
    return ProcedureDraft(tuple(validated))
