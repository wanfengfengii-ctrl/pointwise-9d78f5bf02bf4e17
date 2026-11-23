from dataclasses import dataclass
import sqlite3

from exchange.body import V1Body, decode_body
from procedure.guards import require_id


@dataclass(frozen=True)
class AcceptedPacket:
    packet_id: int
    body: V1Body

    @property
    def procedure_id(self) -> int:
        return self.body.procedure_id


def initialize_catalog(catalog: sqlite3.Connection) -> None:
    if catalog.in_transaction:
        raise RuntimeError('catalog initialization requires an idle connection')
    catalog.execute('''
        CREATE TABLE IF NOT EXISTS accepted_packets (
            packet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            procedure_id INTEGER NOT NULL,
            body TEXT NOT NULL
        )
    ''')


def store_accepted_packet(catalog: sqlite3.Connection, verified_body: str) -> AcceptedPacket:
    """Validate and store the original body text after envelope verification."""
    body = decode_body(verified_body)
    if catalog.in_transaction:
        raise RuntimeError('packet intake requires an idle connection')
    with catalog:
        catalog.execute('BEGIN IMMEDIATE')
        cursor = catalog.execute(
            'INSERT INTO accepted_packets (procedure_id, body) VALUES (?, ?)',
            (body.procedure_id, verified_body),
        )
        packet_id = cursor.lastrowid
    return AcceptedPacket(packet_id, body)


def get_accepted_packet(catalog: sqlite3.Connection, packet_id: int) -> AcceptedPacket | None:
    require_id(packet_id, 'accepted packet')
    row = catalog.execute(
        'SELECT procedure_id, body FROM accepted_packets WHERE packet_id = ?', (packet_id,)
    ).fetchone()
    if row is None:
        return None
    procedure_id, payload = row
    body = decode_body(payload)
    if body.procedure_id != procedure_id:
        raise sqlite3.DatabaseError('catalog parent disagrees with the stored V1 body')
    return AcceptedPacket(packet_id, body)
