"""Direct tests for the sorting desk ledger."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from sorting import SortingDesk


def ticket(specimen='A', report_id=1, category='release', generation=0, **extra):
    return {'specimen': specimen, 'report_id': report_id,
            'category': category, 'generation': generation, **extra}


class SortingDeskTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='jin-heng-sorting-')
        self.addCleanup(directory.cleanup)
        self.db = sqlite3.connect(Path(directory.name) / 'sorting.db')
        self.addCleanup(self.db.close)
        self.desk = SortingDesk(self.db)

    def deliver(self, specimen, item):
        return self.desk.exchange({'op': 'deliver', 'specimen': specimen, 'ticket': item})

    def test_apply_after_delivery(self):
        self.deliver('A', ticket())
        result = self.desk.apply('r1', 'A', ticket())
        self.assertEqual(result, {'request_id': 'r1', 'ticket': ticket()})
        self.assertEqual(self.desk.get_execution('A'), result)

    def test_late_delivery_executes(self):
        # Not delivered first; the desk accepts the original ticket directly.
        result = self.desk.apply('r1', 'A', ticket())
        self.assertEqual(result['ticket'], ticket())

    def test_execution_is_once(self):
        self.deliver('A', ticket(report_id=1))
        first = self.desk.apply('r1', 'A', ticket(report_id=1))
        self.deliver('A', ticket(report_id=2))
        again = self.desk.apply('r2', 'A', ticket(report_id=2))
        self.assertEqual(again, first)
        self.assertEqual(self.desk.get_execution('A')['ticket']['report_id'], 1)

    def test_wrong_specimen_delivery(self):
        with self.assertRaises(ValueError):
            self.deliver('A', ticket(specimen='B'))
        with self.assertRaises(ValueError):
            self.desk.apply('r1', 'A', ticket(specimen='B'))

    def test_changed_ticket_rejected(self):
        self.deliver('A', ticket(report_id=1, category='release'))
        with self.assertRaises(ValueError):
            self.deliver('A', ticket(report_id=1, category='isolate'))

    def test_void_after_category_change(self):
        self.deliver('A', ticket(generation=0))
        applied = self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                                      'category': 'isolate', 'generation': 1})
        self.assertEqual(applied, {'status': 'applied'})
        self.assertIsNone(self.desk.apply('r1', 'A', ticket(generation=0)))

    def test_return_to_original_does_not_restore(self):
        self.deliver('A', ticket(generation=0))
        self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                            'category': 'isolate', 'generation': 1})
        self.desk.exchange({'op': 'revise', 'request_id': 'v2', 'specimen': 'A',
                            'category': 'release', 'generation': 2})
        self.assertIsNone(self.desk.apply('r1', 'A', ticket(generation=0)))

    def test_execution_blocks_category_change(self):
        self.deliver('A', ticket(generation=0))
        self.desk.apply('r1', 'A', ticket(generation=0))
        reply = self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                                    'category': 'isolate', 'generation': 1})
        self.assertEqual(reply, {'status': 'conflict'})

    def test_revision_idempotent(self):
        self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                            'category': 'isolate', 'generation': 1})
        self.assertEqual(
            self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                                'category': 'isolate', 'generation': 1}),
            {'status': 'applied'},
        )
        with self.assertRaises(ValueError):
            self.desk.exchange({'op': 'revise', 'request_id': 'v1', 'specimen': 'A',
                                'category': 'release', 'generation': 2})

    def test_apply_request_params_fixed(self):
        self.deliver('A', ticket(report_id=1))
        self.desk.apply('r1', 'A', ticket(report_id=1))
        with self.assertRaises(ValueError):
            self.desk.apply('r1', 'A', ticket(report_id=2))

    def test_invalid_ticket(self):
        for bad in (None, [], {'specimen': 'A'}, ticket(category='hold'),
                    ticket(generation=-1), ticket(generation=True), ticket(report_id='1')):
            with self.subTest(ticket=bad), self.assertRaises(ValueError):
                self.desk.apply('r', 'A', bad)

    def test_after_commit_only_on_first(self):
        seen = []
        desk = SortingDesk(self.db, after_commit=lambda op, rid: seen.append((op, rid)))
        self.deliver('A', ticket(report_id=1))
        desk.apply('r1', 'A', ticket(report_id=1))
        self.deliver('A', ticket(report_id=2))
        desk.apply('r2', 'A', ticket(report_id=2))
        self.assertEqual(seen, [('apply', 'r1')])


if __name__ == '__main__':
    unittest.main()
