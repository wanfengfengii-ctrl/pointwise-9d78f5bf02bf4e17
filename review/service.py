import sqlite3

from exchange.catalog import get_accepted_packet
from procedure.guards import RationalInput
from report.reconcile import ReconciliationView, reconcile_provenance
from report.sign import sign_report
from review.decision import decide
from review.request import parse_review_request


def review_case(
    catalog: sqlite3.Connection,
    archive: sqlite3.Connection,
    packet_id: int,
    deviation: RationalInput,
) -> ReconciliationView:
    request = parse_review_request(packet_id, deviation)
    packet = get_accepted_packet(catalog, request.packet_id)
    if packet is None:
        raise LookupError(f'unknown accepted packet {request.packet_id}')
    decision = decide(packet, request)
    return reconcile_provenance(sign_report(archive, decision))
