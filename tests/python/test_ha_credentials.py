"""Peer-key transitions fail closed and recover after a lost commit response."""
import json
import unittest
from app.core import ha_credentials as credentials
from app.core.tinyproxy import ConfigError


class PeerCredentialTests(unittest.TestCase):
    def setUp(self):
        self.old = 'a' * 64
        self.peer = credentials.begin_rotation({'key': self.old}, now=1000)
        self.offer = credentials.rotation_offer(self.peer, now=1001)

    def test_staged_key_cannot_read_policy_before_verification(self):
        self.assertTrue(credentials.authorized(self.peer, self.old, now=1001))
        self.assertFalse(credentials.authorized(self.peer, self.offer['key'], now=1001))

    def test_commit_revokes_old_key_and_requires_persisted_key_confirmation(self):
        committed = credentials.commit_rotation(self.peer, self.offer['key'], self.offer['id'], now=1010)
        self.assertFalse(credentials.authorized(committed, self.old, now=1011))
        self.assertTrue(credentials.authorized(committed, self.offer['key'], now=1011))
        with self.assertRaises(ConfigError):
            credentials.begin_rotation(committed, now=1012)
        confirmed = credentials.confirm_rotation(committed, self.offer['key'], self.offer['id'], now=1013)
        self.assertEqual(confirmed['rotation']['state'], 'complete')
        self.assertEqual(credentials.begin_rotation(confirmed, now=1014)['rotation']['state'], 'prepared')

    def test_expired_unverified_transition_rejects_both_keys(self):
        self.assertFalse(credentials.authorized(self.peer, self.old, now=1600))
        self.assertFalse(credentials.authorized(self.peer, self.offer['key'], now=1600))
        with self.assertRaises(ConfigError):
            credentials.commit_rotation(self.peer, self.offer['key'], self.offer['id'], now=1600)
        self.assertEqual(credentials.public_status(self.peer, now=1600)['state'], 'expired')

    def test_lost_commit_response_can_be_retried_after_deadline(self):
        committed = credentials.commit_rotation(self.peer, self.offer['key'], self.offer['id'], now=1010)
        self.assertEqual(credentials.commit_rotation(committed, self.offer['key'], self.offer['id'], now=9999), committed)

    def test_old_key_or_wrong_id_cannot_commit_or_confirm(self):
        for token, identifier in ((self.old, self.offer['id']), (self.offer['key'], '0' * 32)):
            with self.assertRaises(ConfigError):
                credentials.commit_rotation(self.peer, token, identifier, now=1001)
        self.assertEqual(credentials.confirm_rotation(self.peer, self.old, self.offer['id'], now=1001), self.peer)

    def test_overlapping_start_is_rejected_without_mutation(self):
        before = json.dumps(self.peer)
        with self.assertRaises(ConfigError):
            credentials.begin_rotation(self.peer, now=1002)
        self.assertEqual(json.dumps(self.peer), before)

    def test_offer_and_status_are_bounded_and_do_not_expose_keys(self):
        self.assertEqual(credentials.validate_offer(self.offer, now=1001), self.offer)
        for value in ({**self.offer, 'expires': 99999}, {**self.offer, 'key': 'bad'}, []):
            with self.assertRaises(ConfigError):
                credentials.validate_offer(value, now=1001)
        public = json.dumps(credentials.public_status(self.peer, now=1001))
        self.assertNotIn(self.old, public)
        self.assertNotIn(self.offer['key'], public)
        self.assertNotIn(self.offer['id'], public)

    def test_malformed_auth_state_does_not_accept_current_key(self):
        for rotation in ([], {'state': 'unexpected'}, {'state': 'prepared', 'expires': 'never'}):
            self.assertFalse(credentials.authorized({'key': self.old, 'rotation': rotation}, self.old, now=1001))
