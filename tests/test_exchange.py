from dataclasses import replace
import json

from exchange.accept import receive_packet
from exchange.catalog import get_accepted_packet, initialize_catalog
from exchange.envelope import seal_v1
from exchange.issue import issue_packet
from procedure.freeze import freeze_procedure
from reference.ingest import record_reference
from tests.support import LabTestCase, REFERENCE_NAME, priority_draft, single_rule


class ExchangeTests(LabTestCase):
    def test_changed_payload(self):
        frozen = freeze_procedure(self.source, single_rule(scale=2))
        envelope = issue_packet(self.source, frozen.procedure_id)
        original = receive_packet(self.catalog, envelope)
        document = json.loads(envelope.body)
        document['rules'][0]['conversion']['scale'] = '3'
        with self.assertRaises(ValueError):
            receive_packet(self.catalog, replace(envelope, body=json.dumps(document)))
        self.assertEqual(self.catalog_count(), 1)
        self.assertEqual(get_accepted_packet(self.catalog, original.packet_id), original)
        next_packet = receive_packet(self.catalog, envelope)
        self.assertEqual(next_packet.packet_id, original.packet_id + 1)

    def test_public_projection(self):
        record_reference(self.source, REFERENCE_NAME, 2000, 'nm', 'private-bench-ticket')
        frozen = freeze_procedure(self.source, priority_draft())
        envelope = issue_packet(self.source, frozen.procedure_id)
        document = json.loads(envelope.body)
        self.assertEqual(document, {
            'procedure_id': frozen.procedure_id,
            'rules': [
                {
                    'rule_id': 'PRIMARY',
                    'interval': {'lower': None, 'upper': '5', 'lower_closed': False, 'upper_closed': True},
                    'conversion': {'scale': '1', 'offset': '1', 'limit': '12', 'include_limit': True},
                },
                {
                    'rule_id': 'BACKUP',
                    'interval': {'lower': None, 'upper': None, 'lower_closed': False, 'upper_closed': False},
                    'conversion': {'scale': '2', 'offset': '0', 'limit': '14', 'include_limit': True},
                },
            ],
        })
        for private in ('2000', 'nm', REFERENCE_NAME, 'private-bench-ticket', 'reference_factor', 'raw_value'):
            self.assertNotIn(private, envelope.body)
        packet = receive_packet(self.catalog, envelope)
        self.close_source()
        self.assert_decision(self.review(packet, 5), packet, 'pass', 'PRIMARY', 6)

    def test_invalid_v1(self):
        frozen = freeze_procedure(self.source, single_rule())
        envelope = issue_packet(self.source, frozen.procedure_id)
        document = json.loads(envelope.body)
        del document['rules'][0]['rule_id']
        bad_members = seal_v1(json.dumps(document))
        for invalid in (
            replace(envelope, version=2),
            replace(envelope, version=True),
            bad_members,
            seal_v1('{"procedure_id":1,"procedure_id":2,"rules":[]}'),
            seal_v1('{}'),
        ):
            with self.subTest(envelope=invalid), self.assertRaises(ValueError):
                receive_packet(self.catalog, invalid)
        self.assertEqual(self.catalog_count(), 0)

    def test_reinit_catalog(self):
        original = self.accept_draft(single_rule(scale=2))
        latest = self.accept_draft(single_rule(scale=4))
        initialize_catalog(self.catalog)
        initialize_catalog(self.catalog)
        self.assertEqual(self.catalog_count(), 2)
        self.assertEqual(get_accepted_packet(self.catalog, original.packet_id), original)
        self.assertEqual(get_accepted_packet(self.catalog, latest.packet_id), latest)
        self.close_source()
        self.assert_decision(self.review(original, 3), original, 'pass', 'SINGLE', 6)
