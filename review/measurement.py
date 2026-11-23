from fractions import Fraction

from procedure.conversion import AffineConversion


def compute_measurement(conversion: AffineConversion, deviation: Fraction) -> Fraction:
    return conversion.scale * deviation + conversion.offset


def meets_quality_limit(conversion: AffineConversion, value: Fraction) -> bool:
    if conversion.include_limit:
        return value <= conversion.limit
    return value < conversion.limit
