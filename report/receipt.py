from dataclasses import dataclass


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: int
    report_id: int
    packet_id: int
    procedure_id: int


def make_first_receipt(receipt_id: int, report_id: int, packet_id: int, procedure_id: int) -> FirstReceipt:
    return FirstReceipt(receipt_id, report_id, packet_id, procedure_id)
