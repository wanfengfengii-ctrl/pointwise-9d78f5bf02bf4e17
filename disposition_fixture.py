from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from exchange.accept import receive_packet
from exchange.catalog import initialize_catalog
from exchange.issue import issue_packet
from procedure.conversion import ConversionSpec
from procedure.edit import DraftRule, ProcedureDraft
from procedure.freeze import freeze_procedure, initialize_procedure_store
from procedure.guards import RationalInterval
from report.archive import initialize_archive
from report.history import get_report
from review.service import review_case

DEVIATION = "2"
EXAMPLES = {
    "R6": (3, "pass", 6),
    "R8": (4, "pass", 8),
    "I12": (6, "fail", 12),
    "R4": (2, "pass", 4),
}


def prepare_examples(source, catalog):
    initialize_procedure_store(source)
    initialize_catalog(catalog)
    packets = {}
    for rule_id, (scale, _, _) in EXAMPLES.items():
        rule = DraftRule(rule_id, RationalInterval(), ConversionSpec(scale, 0, 10))
        frozen = freeze_procedure(source, ProcedureDraft((rule,)))
        packets[rule_id] = receive_packet(catalog, issue_packet(source, frozen.procedure_id))
    return packets


@contextmanager
def workbench(root):
    root = Path(root)
    with (
        closing(sqlite3.connect(root / "intake.db")) as catalog,
        closing(sqlite3.connect(root / "reports.db")) as archive,
        closing(sqlite3.connect(root / "sorting.db")) as sorting,
    ):
        initialize_archive(archive)
        with closing(sqlite3.connect(root / "source.db")) as source:
            packets = prepare_examples(source, catalog)
        yield catalog, archive, sorting, packets


class DropReply:
    def __init__(self, operation, request_id):
        self.target = (operation, request_id)
        self.triggered = False

    def __call__(self, operation, request_id):
        if not self.triggered and (operation, request_id) == self.target:
            self.triggered = True
            raise TimeoutError(f"reply lost for {operation} {request_id}")


def verify_examples(catalog, archive, packets):
    reports = {}
    for name, (_, status, value) in EXAMPLES.items():
        packet = packets[name]
        view = review_case(catalog, archive, packet.packet_id, DEVIATION)
        assert (view.decision.status, view.decision.value) == (status, value), view
        assert view.decision.packet_id == packet.packet_id, view
        assert (view.provenance.procedure_id, view.provenance.rule_id) == (packet.procedure_id, name), view
        reports[name] = view
    return reports


def main():
    with TemporaryDirectory(prefix="jin-heng-disposition-") as directory:
        root = Path(directory)
        with workbench(root) as (catalog, archive, sorting, packets):
            assert archive.execute("PRAGMA database_list").fetchone()[2] != sorting.execute(
                "PRAGMA database_list"
            ).fetchone()[2]
            reports = verify_examples(catalog, archive, packets)
        with closing(sqlite3.connect(root / "reports.db")) as reopened:
            for name, before in reports.items():
                view = get_report(reopened, before.report_id)
                assert view == before, (name, view)
                print(f"{name} {view.decision.value} μm {view.decision.status} / report {view.report_id} / receipt {view.first_receipt.receipt_id}")


if __name__ == "__main__":
    main()
