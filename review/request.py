from dataclasses import dataclass
from fractions import Fraction

from procedure.guards import RationalInput, parse_rational, require_id


@dataclass(frozen=True)
class ReviewRequest:
    packet_id: int
    deviation: Fraction


def parse_review_request(packet_id: int, deviation: RationalInput) -> ReviewRequest:
    return ReviewRequest(require_id(packet_id, 'accepted packet'), parse_rational(deviation))
