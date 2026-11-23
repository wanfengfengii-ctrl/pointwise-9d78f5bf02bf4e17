from dataclasses import dataclass
from fractions import Fraction

from procedure.guards import RationalInput, parse_rational


@dataclass(frozen=True)
class ConversionSpec:
    scale: RationalInput
    offset: RationalInput
    limit: RationalInput
    include_limit: bool = True
    reference_factor: RationalInput = 0


@dataclass(frozen=True)
class AffineConversion:
    scale: Fraction
    offset: Fraction
    limit: Fraction
    include_limit: bool


def validate_conversion(spec: ConversionSpec) -> ConversionSpec:
    if not isinstance(spec, ConversionSpec) or type(spec.include_limit) is not bool:
        raise ValueError('conversion requires a boolean quality boundary')
    return ConversionSpec(
        scale=parse_rational(spec.scale),
        offset=parse_rational(spec.offset),
        limit=parse_rational(spec.limit),
        include_limit=spec.include_limit,
        reference_factor=parse_rational(spec.reference_factor),
    )


def bind_conversion(spec: ConversionSpec, reference: Fraction | None) -> AffineConversion:
    """Bind a validated spec to a present reference or to an independent rule."""
    if reference is None:
        if spec.reference_factor != 0:
            raise ValueError('the conversion needs a reference value')
        offset = spec.offset
    else:
        offset = spec.offset + spec.reference_factor * reference
    return AffineConversion(spec.scale, offset, spec.limit, spec.include_limit)
