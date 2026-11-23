from dataclasses import dataclass
from fractions import Fraction

from procedure.guards import RationalInput, parse_rational, require_text


@dataclass(frozen=True)
class ReferenceReading:
    name: str
    raw_value: Fraction
    unit: str
    provenance: str


def parse_reference_reading(
    name: str, reading: RationalInput, unit: str, provenance: str
) -> ReferenceReading:
    return ReferenceReading(
        require_text(name, 'reference name'),
        parse_rational(reading),
        require_text(unit, 'reading unit'),
        require_text(provenance, 'reference provenance'),
    )
