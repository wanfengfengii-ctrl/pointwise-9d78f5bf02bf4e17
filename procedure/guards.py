from dataclasses import dataclass
from fractions import Fraction
import re


RationalInput = int | str | Fraction
_RATIONAL = re.compile(r'[+-]?(?:[0-9]+(?:/[0-9]+|\.[0-9]*)?|\.[0-9]+)')
_MAX_SQLITE_ID = (1 << 63) - 1


def require_id(value: int, field: str) -> int:
    if type(value) is not int or not 0 < value <= _MAX_SQLITE_ID:
        raise ValueError(f'{field} must be a positive SQLite integer')
    return value


def require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonempty text')
    value.encode('utf-8')
    return value


def _parse_digits(digits: str) -> int:
    value = 0
    for start in range(0, len(digits), 9):
        chunk = digits[start:start + 9]
        value = value * 10 ** len(chunk) + int(chunk)
    return value


def parse_rational(value: RationalInput) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if type(value) is int:
        return Fraction(value)
    if not isinstance(value, str) or _RATIONAL.fullmatch(value) is None:
        raise ValueError('expected an exact rational number')
    sign = -1 if value.startswith('-') else 1
    unsigned = value[1:] if value[0] in '+-' else value
    if '/' in unsigned:
        numerator, denominator = unsigned.split('/')
        divisor = _parse_digits(denominator)
        if divisor == 0:
            raise ValueError('rational denominator must be positive')
        return Fraction(sign * _parse_digits(numerator), divisor)
    whole, _, decimal = unsigned.partition('.')
    return Fraction(sign * _parse_digits(whole + decimal), 10 ** len(decimal))


def _format_integer(value: int) -> str:
    # Convert in chunks to keep large exact values independent of Python's digit limit.
    sign = '-' if value < 0 else ''
    remaining = abs(value)
    chunks = []
    while remaining >= 10 ** 9:
        remaining, chunk = divmod(remaining, 10 ** 9)
        chunks.append(f'{chunk:09d}')
    return sign + str(remaining) + ''.join(reversed(chunks))


def format_rational(value: Fraction) -> str:
    numerator = _format_integer(value.numerator)
    if value.denominator == 1:
        return numerator
    return numerator + '/' + _format_integer(value.denominator)


@dataclass(frozen=True)
class RationalInterval:
    lower: RationalInput | None = None
    upper: RationalInput | None = None
    lower_closed: bool = False
    upper_closed: bool = False


def validate_interval(interval: RationalInterval) -> RationalInterval:
    if not isinstance(interval, RationalInterval):
        raise ValueError('expected a rational interval')
    if type(interval.lower_closed) is not bool or type(interval.upper_closed) is not bool:
        raise ValueError('interval closure flags must be booleans')
    lower = None if interval.lower is None else parse_rational(interval.lower)
    upper = None if interval.upper is None else parse_rational(interval.upper)
    if (lower is None and interval.lower_closed) or (upper is None and interval.upper_closed):
        raise ValueError('an unbounded end cannot be closed')
    if lower is not None and upper is not None:
        if lower > upper or (lower == upper and not (interval.lower_closed and interval.upper_closed)):
            raise ValueError('interval must be nonempty and ordered')
    return RationalInterval(lower, upper, interval.lower_closed, interval.upper_closed)


def matches_interval(interval: RationalInterval, value: Fraction) -> bool:
    """Test a normalized interval from validation or the packet decoder."""
    lower = interval.lower
    if lower is not None and (value < lower or (value == lower and not interval.lower_closed)):
        return False
    upper = interval.upper
    if upper is not None and (value > upper or (value == upper and not interval.upper_closed)):
        return False
    return True
