from dataclasses import dataclass
import sqlite3

from report.archive import SignedReportSnapshot
from report.receipt import FirstReceipt
from review.decision import Decision


@dataclass(frozen=True)
class RuleProvenance:
    procedure_id: int
    rule_id: str | None


@dataclass(frozen=True)
class ReconciliationView:
    report_id: int
    decision: Decision
    first_receipt: FirstReceipt
    provenance: RuleProvenance


def reconcile_provenance(snapshot: SignedReportSnapshot) -> ReconciliationView:
    decision = snapshot.decision
    receipt = snapshot.first_receipt
    expected = (snapshot.report_id, decision.packet_id, decision.procedure_id)
    if (receipt.report_id, receipt.packet_id, receipt.procedure_id) != expected:
        raise sqlite3.DatabaseError(f'first receipt disagrees with report {snapshot.report_id}')
    return ReconciliationView(
        snapshot.report_id,
        decision,
        receipt,
        RuleProvenance(decision.procedure_id, decision.rule_id),
    )
