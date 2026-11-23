from fractions import Fraction
from types import MappingProxyType


UNIT_FACTORS = MappingProxyType({'nm': Fraction(1, 1000), 'μm': Fraction(1)})


def normalize_reading(value: Fraction, unit: str) -> Fraction:
    try:
        factor = UNIT_FACTORS[unit]
    except (KeyError, TypeError) as error:
        raise ValueError(f'unsupported reading unit {unit!r}') from error
    return value * factor
