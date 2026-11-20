"""Reject restored/outdated primary policy without rejecting an explicit newer rollback."""
import unittest
from app.core import ha_policy_order as ordering


class PolicyOrderingTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(revision='a' * 64, generation=dict(epoch='b' * 32, sequence=7))

    def test_identical_publication_is_idempotent(self):
        self.assertEqual(ordering.next_publication(self.old, self.old['revision']), self.old['generation'])
        ordering.verify(self.old, self.old)

    def test_explicit_rollback_gets_a_new_sequence(self):
        generation = ordering.next_publication(self.old, 'c' * 64)
        self.assertEqual(generation, dict(epoch='b' * 32, sequence=8))
        ordering.verify(dict(revision='c' * 64, generation=generation), self.old)

    def test_restored_primary_unknown_epoch_and_protocol_downgrade_are_rejected(self):
        for generation in (None, dict(epoch='b' * 32, sequence=6), dict(epoch='c' * 32, sequence=8)):
            with self.subTest(generation=generation), self.assertRaises(ordering.PolicyOrderError):
                ordering.verify(dict(revision='a' * 64, generation=generation), self.old)

    def test_same_sequence_cannot_refer_to_different_contents(self):
        with self.assertRaises(ordering.PolicyOrderError):
            ordering.verify(dict(revision='c' * 64, generation=self.old['generation']), self.old)

    def test_legacy_policy_can_upgrade_once(self):
        ordering.verify(dict(revision='a' * 64), dict(revision='a' * 64))
        ordering.verify(self.old, dict(revision='a' * 64))
        self.assertTrue(ordering.valid(ordering.next_publication(None, 'a' * 64)))

    def test_malformed_generation_and_exhaustion_fail_closed(self):
        for generation in ([], dict(epoch='bad', sequence=1), dict(epoch='b' * 32, sequence=True),
                           dict(epoch='b' * 32, sequence=0)):
            with self.subTest(generation=generation), self.assertRaises(ordering.PolicyOrderError):
                ordering.verify(dict(revision='a' * 64, generation=generation), self.old)
        with self.assertRaises(ordering.PolicyOrderError):
            ordering.next_publication(dict(revision='a', generation=dict(epoch='b' * 32, sequence=2 ** 53 - 1)), 'new')
