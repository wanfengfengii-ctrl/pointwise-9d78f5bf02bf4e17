from dataclasses import dataclass
from fractions import Fraction
import json

from procedure.conversion import AffineConversion
from procedure.guards import (
    RationalInterval,
    format_rational,
    parse_rational,
    require_id,
    require_text,
    validate_interval,
)


@dataclass(frozen=True)
class V1Rule:
    rule_id: str
    interval: RationalInterval
    conversion: AffineConversion | None


@dataclass(frozen=True)
class V1Body:
    procedure_id: int
    rules: tuple[V1Rule, ...]


def _bound_text(value: Fraction | None) -> str | None:
    return None if value is None else format_rational(value)


def _encode_rule(rule: V1Rule) -> dict:
    interval = rule.interval
    conversion = rule.conversion
    return {
        'rule_id': rule.rule_id,
        'interval': {
            'lower': _bound_text(interval.lower),
            'upper': _bound_text(interval.upper),
            'lower_closed': interval.lower_closed,
            'upper_closed': interval.upper_closed,
        },
        'conversion': None if conversion is None else {
            'scale': format_rational(conversion.scale),
            'offset': format_rational(conversion.offset),
            'limit': format_rational(conversion.limit),
            'include_limit': conversion.include_limit,
        },
    }


def encode_body(body: V1Body) -> str:
    """Encode a producer-built body containing normalized intervals and coefficients."""
    return json.dumps(
        {'procedure_id': body.procedure_id, 'rules': [_encode_rule(rule) for rule in body.rules]},
        sort_keys=True,
        separators=(',', ':'),
    )


def _unique_members(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate V1 member {key!r}')
        result[key] = value
    return result


def _require_members(document: object, fields: set[str], label: str) -> None:
    if not isinstance(document, dict) or document.keys() != fields:
        raise ValueError(f'invalid V1 {label} members')


def _rational_text(text: object) -> Fraction:
    if not isinstance(text, str):
        raise ValueError('V1 rational values must be strings')
    value = parse_rational(text)
    if text != format_rational(value):
        raise ValueError('V1 rational values must be in reduced form')
    return value


def _decode_interval(document: dict) -> RationalInterval:
    _require_members(document, {'lower', 'upper', 'lower_closed', 'upper_closed'}, 'interval')
    lower = document['lower']
    upper = document['upper']
    return validate_interval(RationalInterval(
        None if lower is None else _rational_text(lower),
        None if upper is None else _rational_text(upper),
        document['lower_closed'],
        document['upper_closed'],
    ))


def _decode_conversion(document: dict | None) -> AffineConversion | None:
    if document is None:
        return None
    _require_members(document, {'scale', 'offset', 'limit', 'include_limit'}, 'conversion')
    if type(document['include_limit']) is not bool:
        raise ValueError('V1 quality boundary must be a boolean')
    return AffineConversion(
        _rational_text(document['scale']),
        _rational_text(document['offset']),
        _rational_text(document['limit']),
        document['include_limit'],
    )


def decode_body(payload: str) -> V1Body:
    if not isinstance(payload, str):
        raise ValueError('V1 body must be JSON text')
    try:
        document = json.loads(payload, object_pairs_hook=_unique_members)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid V1 JSON body') from error
    _require_members(document, {'procedure_id', 'rules'}, 'body')
    procedure_id = require_id(document['procedure_id'], 'V1 procedure')
    if not isinstance(document['rules'], list) or not document['rules']:
        raise ValueError('V1 body requires an ordered list of rules')

    seen = set()
    rules = []
    for rule in document['rules']:
        _require_members(rule, {'rule_id', 'interval', 'conversion'}, 'rule')
        rule_id = require_text(rule['rule_id'], 'V1 rule id')
        if rule_id in seen:
            raise ValueError(f'duplicate V1 rule id {rule_id!r}')
        seen.add(rule_id)
        rules.append(V1Rule(rule_id, _decode_interval(rule['interval']), _decode_conversion(rule['conversion'])))
    return V1Body(procedure_id, tuple(rules))
