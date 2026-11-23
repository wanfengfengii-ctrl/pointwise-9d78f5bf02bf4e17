import sqlite3

from exchange.catalog import AcceptedPacket, store_accepted_packet
from exchange.envelope import V1Envelope, verify_v1


def receive_packet(catalog: sqlite3.Connection, envelope: V1Envelope) -> AcceptedPacket:
    verified_body = verify_v1(envelope)
    return store_accepted_packet(catalog, verified_body)
