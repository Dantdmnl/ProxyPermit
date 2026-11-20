"""No unknown certificate is trusted, including after a failed handover."""
import unittest
from app.core import ha_certificates as certificates
from app.core.tinyproxy import ConfigError


class CertificateTransitionTests(unittest.TestCase):
    def setUp(self):
        self.peer = {'pin': 'a' * 64, 'key': 'b' * 64}
        self.offer = dict(id='c' * 32, oldPin='a' * 64, newPin='d' * 64, created=1000, expires=1600, phase='offered')

    def test_candidate_is_learned_only_through_an_existing_trusted_pin(self):
        with self.assertRaises(ConfigError):
            certificates.stage(self.peer, self.offer, 'e' * 64, now=1001)
        staged = certificates.stage(self.peer, self.offer, self.peer['pin'], now=1001)
        self.assertEqual(certificates.allowed_pins(staged, now=1002), ['a' * 64, 'd' * 64])

    def test_new_handshake_is_not_enough_without_a_commit_decision(self):
        staged = certificates.stage(self.peer, self.offer, self.peer['pin'], now=1001)
        self.assertEqual(certificates.promote(staged, self.offer, 'd' * 64, now=1002), staged)
        committed = {**self.offer, 'phase': 'committed'}
        self.assertEqual(certificates.promote(staged, committed, 'a' * 64, now=1002), staged)
        active = certificates.promote(staged, committed, 'd' * 64, now=1002)
        self.assertEqual(certificates.allowed_pins(active, now=1003), ['d' * 64])
        self.assertEqual(active['key'], self.peer['key'])
        self.assertNotIn('certificatePending', active)

    def test_expired_candidate_does_not_expand_trust(self):
        staged = certificates.stage(self.peer, self.offer, self.peer['pin'], now=1001)
        self.assertEqual(certificates.allowed_pins(staged, now=1600), ['a' * 64])
        self.assertEqual(certificates.prune(staged, now=1600), self.peer)
        self.assertEqual(certificates.stage(self.peer, self.offer, 'a' * 64, now=1600), self.peer)

    def test_malformed_or_concurrent_handover_is_rejected(self):
        staged = certificates.stage(self.peer, self.offer, self.peer['pin'], now=1001)
        for offer in ({**self.offer, 'expires': 9999}, {**self.offer, 'id': 'e' * 32},
                      {**self.offer, 'oldPin': 'f' * 64}, {**self.offer, 'newPin': 'a' * 64}):
            with self.subTest(offer=offer), self.assertRaises(ConfigError):
                certificates.stage(staged, offer, 'a' * 64, now=1002)

    def test_committed_version_cannot_reintroduce_the_old_pin(self):
        committed = {**self.offer, 'phase': 'committed'}
        staged = certificates.stage(self.peer, self.offer, self.peer['pin'], now=1001)
        active = certificates.promote(staged, committed, 'd' * 64, now=1002)
        self.assertEqual(certificates.stage(active, committed, 'd' * 64, now=1003), active)
        self.assertEqual(certificates.allowed_pins(active, now=1003), ['d' * 64])

    def test_fresh_pairing_to_committed_leaf_never_learns_the_previous_pin(self):
        peer = {**self.peer, 'pin': 'd' * 64}
        active = certificates.stage(peer, {**self.offer, 'phase': 'committed'}, 'd' * 64, now=9999)
        self.assertEqual(active['certificateVersion'], self.offer['id'])
        self.assertEqual(certificates.allowed_pins(active, now=9999), ['d' * 64])
