"""Status is derived from quorum, TLS witness and resource evidence, never secrets."""
import json
import unittest

from app.core.cluster_evidence import summarize, xml

CIB = '''<cib><configuration><nodes><node id="1" uname="node-a"/><node id="2" uname="node-b"/></nodes><crm_config><cluster_property_set>
<nvpair name="stonith-enabled" value="true"/>
<nvpair name="startup-fencing" value="true"/>
<nvpair name="no-quorum-policy" value="stop"/>
</cluster_property_set></crm_config><resources>
<primitive id="fencer" class="stonith" type="fence_xvm"><instance_attributes>
<nvpair name="pcmk_host_check" value="static-list"/>
<nvpair name="pcmk_host_list" value="node-a node-b"/>
<nvpair name="pcmk_host_map" value="node-a:vm-a;node-b:vm-b"/>
<nvpair name="password" value="private-fixture-value"/></instance_attributes></primitive>
<primitive id="proxypermit-vip" class="ocf" provider="proxypermit" type="vip">
<meta_attributes><nvpair name="requires" value="fencing"/></meta_attributes></primitive>
</resources></configuration></cib>'''
MONITOR = '''<crm_mon><summary><current_dc present="true" with_quorum="true"/></summary><resources>
<resource id="fencer" resource_agent="stonith:fence_xvm" active="true" failed="false" managed="true"/>
<resource id="proxypermit-vip" active="true" managed="true" nodes_running_on="1">
<node name="node-a"/></resource></resources></crm_mon>'''
DEVICE = 'Expected votes:\t3\nState:\tConnected\nTLS active:\tYes (client certificate sent)\n'
CRYPTO = 'totem.crypto_cipher (str) = aes256\ntotem.crypto_hash (str) = sha256\n'


class ClusterEvidenceTests(unittest.TestCase):
    def evidence(self, cib=CIB, monitor=MONITOR, device=DEVICE, crypto=CRYPTO, local=True, node='node-a'):
        return summarize(cib, monitor, device, crypto, local, node)

    def test_healthy_local_and_peer_ownership(self):
        self.assertEqual(self.evidence()['state'], 'protected')
        peer = self.evidence(local=False, node='node-b')
        self.assertEqual(peer['state'], 'protected')
        self.assertNotIn('private-fixture-value', json.dumps(peer))
        self.assertNotIn('password', json.dumps(peer))

    def test_missing_quorum_witness_or_encryption_is_degraded(self):
        for values in ({'monitor': MONITOR.replace('with_quorum="true"', 'with_quorum="false"')},
                       {'device': DEVICE.replace('Connected', 'Disconnected')},
                       {'device': DEVICE.replace('Yes (client certificate sent)', 'No')},
                       {'crypto': CRYPTO.replace('aes256', 'none')},
                       {'cib': CIB.replace('name="stonith-enabled" value="true"', 'name="stonith-enabled" value="false"')},
                       {'cib': CIB.replace('value="stop"', 'value="ignore"')}):
            with self.subTest(values=values):
                self.assertEqual(self.evidence(**values)['state'], 'degraded')

    def test_known_fencer_failure_is_not_healthy(self):
        monitor = MONITOR.replace('resource_agent="stonith:fence_xvm" active="true" failed="false"',
                                  'resource_agent="stonith:fence_xvm" active="false" failed="true"')
        self.assertEqual(self.evidence(monitor=monitor)['state'], 'degraded')

    def test_fencer_must_explicitly_cover_both_distinct_nodes(self):
        for value in (CIB.replace('static-list', 'none'), CIB.replace('node-a node-b', 'node-a'),
                      CIB.replace('node-b:vm-b', 'node-b:vm-a'), CIB.replace('node-b:vm-b', 'node-c:vm-b')):
            status = self.evidence(cib=value)
            self.assertEqual(status['state'], 'degraded')
            self.assertFalse(status['fencingConfigured'])

    def test_conflicting_resource_or_unmanaged_local_vip_is_unsafe(self):
        monitor = MONITOR.replace('nodes_running_on="1"', 'nodes_running_on="2"')
        self.assertEqual(self.evidence(monitor=monitor)['state'], 'unsafe')
        self.assertEqual(self.evidence(node='node-b', local=True)['state'], 'unsafe')

    def test_stopped_and_unknown_resources_are_distinct(self):
        monitor = MONITOR.replace('nodes_running_on="1"', 'nodes_running_on="0"').replace('<node name="node-a"/>', '')
        self.assertEqual(self.evidence(monitor=monitor, local=False)['state'], 'stopped')
        self.assertEqual(self.evidence(monitor='<crm_mon/>', local=False)['state'], 'unknown')

    def test_xml_entities_are_never_expanded(self):
        with self.assertRaises(ValueError):
            xml('<!DOCTYPE cib [<!ENTITY secret "private-fixture">]><cib>&secret;</cib>')
