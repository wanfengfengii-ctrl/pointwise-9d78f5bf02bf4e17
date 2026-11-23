from fractions import Fraction

from procedure.guards import RationalInterval
from report.history import get_report
from review.service import review_case
from tests.support import LabTestCase, single_rule


class ReviewTests(LabTestCase):
    def fractional_packet(self, *, include_limit=True):
        return self.accept_draft(single_rule(
            offset='1/2', limit='3/4', interval=RationalInterval(0, 1, True, True),
            include_limit=include_limit,
        ))

    def test_no_rule(self):
        packet = self.fractional_packet()
        self.close_source()
        result = self.review(packet, '3/2')
        self.assert_decision(result, packet, 'no_rule', None, None)
        self.assertEqual(result.decision.deviation, Fraction(3, 2))
        self.assertEqual(get_report(self.archive, result.report_id), result)

    def test_fractional_inside(self):
        packet = self.fractional_packet()
        self.close_source()
        result = self.review(packet, '1/3')
        self.assert_decision(result, packet, 'fail', 'SINGLE', Fraction(5, 6))
        self.assertEqual(get_report(self.archive, result.report_id), result)

    def test_invalid_deviation(self):
        packet = self.fractional_packet()
        self.close_source()
        original = self.review(packet, '1/4')
        for deviation in ('1/0', 'NaN', '0.1.2', None, True, 0.25):
            with self.subTest(deviation=deviation), self.assertRaises(ValueError):
                self.review(packet, deviation)
        with self.assertRaises(LookupError):
            review_case(self.catalog, self.archive, packet.packet_id + 1, '1/4')
        with self.assertRaises(ValueError):
            review_case(self.catalog, self.archive, True, '1/4')
        self.assertEqual(self.archive_counts(), (1, 1))
        self.assertEqual(get_report(self.archive, original.report_id), original)

    def test_quality_boundary_belongs_to_the_rule(self):
        closed = self.fractional_packet()
        opened = self.fractional_packet(include_limit=False)
        self.close_source()
        self.assert_decision(self.review(closed, '1/4'), closed, 'pass', 'SINGLE', Fraction(3, 4))
        self.assert_decision(self.review(opened, '1/4'), opened, 'fail', 'SINGLE', Fraction(3, 4))

    def test_large_exact_rational_survives_storage(self):
        numerator = 10 ** 4500 + 1
        packet = self.accept_draft(single_rule(scale=2, limit=numerator))
        self.close_source()
        result = self.review(packet, Fraction(numerator, 3))
        self.assert_decision(result, packet, 'pass', 'SINGLE', Fraction(2 * numerator, 3))
        self.reopen_archive()
        saved = get_report(self.archive, result.report_id)
        self.assertTrue(saved == result)
