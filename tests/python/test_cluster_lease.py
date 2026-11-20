"""Maintenance ownership and standby restoration are one versioned CIB change."""
from xml.etree import ElementTree as ET
import unittest

from app.core import cluster_lease as lease

CIB = '<cib><configuration><nodes><node id="1" uname="node-a"/><node id="2" uname="node-b"/></nodes></configuration><status/></cib>'


class ClusterLeaseTests(unittest.TestCase):
    def test_claim_reserves_cluster_and_puts_only_local_node_in_standby(self):
        root = ET.fromstring(CIB)
        saved = lease.claim(root, 'node-b', 'a' * 32, 1000)
        self.assertEqual(lease.marker(root), dict(node='node-b', token='a' * 32))
        self.assertIsNone(root.find("configuration/nodes/node[@uname='node-a']//nvpair[@name='standby']"))
        self.assertEqual(root.find("configuration/nodes/node[@uname='node-b']//nvpair[@name='standby']").get('value'), 'on')
        lease.release(root, saved)
        self.assertEqual(ET.tostring(root), ET.tostring(ET.fromstring(CIB)))

    def test_second_claim_and_wrong_owner_cannot_steal_reservation(self):
        root = ET.fromstring(CIB)
        saved = lease.claim(root, 'node-a', 'a' * 32, 1000)
        before = ET.tostring(root)
        with self.assertRaises(lease.MaintenanceConflict):
            lease.claim(root, 'node-b', 'b' * 32, 9999999999)
        with self.assertRaises(lease.MaintenanceConflict):
            lease.release(root, {**saved, 'token': 'b' * 32})
        self.assertEqual(ET.tostring(root), before)

    def test_existing_standby_setting_is_restored_exactly(self):
        for value in ('on', 'off', 'true', 'false'):
            root = ET.fromstring(CIB)
            node = root.find('configuration/nodes/node')
            attributes = ET.SubElement(node, 'instance_attributes', id='operator-settings')
            ET.SubElement(attributes, 'nvpair', id='operator-standby', name='standby', value=value)
            before = ET.tostring(root)
            saved = lease.claim(root, 'node-a', 'a' * 32, 1000)
            lease.release(root, saved)
            self.assertEqual(ET.tostring(root), before)

    def test_operator_change_is_preserved(self):
        root = ET.fromstring(CIB)
        saved = lease.claim(root, 'node-a', 'a' * 32, 1000)
        pair = root.find(".//nvpair[@name='standby']")
        pair.set('value', 'off')
        before = ET.tostring(root)
        with self.assertRaises(lease.MaintenanceConflict):
            lease.release(root, saved)
        self.assertEqual(ET.tostring(root), before)

    def test_transient_standby_overrides_require_os_review(self):
        root = ET.fromstring(CIB)
        state = ET.SubElement(root.find('status'), 'node_state', id='1')
        attributes = ET.SubElement(ET.SubElement(state, 'transient_attributes'), 'instance_attributes')
        ET.SubElement(attributes, 'nvpair', name='standby', value='off')
        with self.assertRaises(lease.MaintenanceConflict):
            lease.claim(root, 'node-a', 'a' * 32, 1000)

    def test_ambiguous_nodes_and_invalid_tokens_are_rejected(self):
        for token in ('bad', None, True):
            with self.assertRaises(lease.MaintenanceConflict):
                lease.claim(ET.fromstring(CIB), 'node-a', token, 1000)
        root = ET.fromstring(CIB)
        ET.SubElement(root.find('configuration/nodes'), 'node', id='3', uname='node-c')
        with self.assertRaises(lease.MaintenanceConflict):
            lease.claim(root, 'node-a', 'a' * 32, 1000)
