"""Status is derived from quorum, TLS witness and resource evidence, never secrets."""
import json
import unittest
from unittest.mock import patch

from app.core.cluster_evidence import summarize, xml, fencing_coverage
from scripts.deploy import cluster_status
from xml.etree import ElementTree as ET

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
QUORUM = 'Quorate:          Yes\nExpected votes:   3\nQuorum:           2  \nFlags:            Quorate WaitForAll Qdevice \n'


class ClusterEvidenceTests(unittest.TestCase):
    def evidence(self, cib=CIB, monitor=MONITOR, device=DEVICE, crypto=CRYPTO, local=True,
                 node='node-a', quorum=QUORUM, native_credential='unknown'):
        return summarize(cib, monitor, device, crypto, local, node, quorum_text=quorum,
                         native_credential=native_credential)

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

    def test_stale_qdevice_vote_count_is_checked_against_live_votequorum(self):
        rejoined = DEVICE.replace('votes:\t3', 'votes:\t2')
        self.assertEqual(self.evidence(device=rejoined)['state'], 'protected')
        for unsafe in ('', QUORUM.replace('votes:   3', 'votes:   2'), QUORUM.replace(' Qdevice', ''),
                       QUORUM.replace('Quorate:          Yes', 'Quorate:          No')):
            self.assertEqual(self.evidence(device=rejoined, quorum=unsafe)['state'], 'degraded')
        self.assertEqual(self.evidence(device=rejoined.replace('client certificate sent', 'no client certificate'))['state'], 'degraded')

    def test_shared_fencer_cannot_hide_expired_local_peer_credential(self):
        cib = CIB.replace('fence_xvm', 'fence_proxypermit_pve').replace('node-a:vm-a;node-b:vm-b', 'node-a:101;node-b:102')
        cib = cib.replace('<instance_attributes>', '<instance_attributes><nvpair name="ipaddr" value="pve.example"/>'
            '<nvpair name="ssl" value="true"/>', 1)
        monitor = MONITOR.replace('fence_xvm', 'fence_proxypermit_pve')
        self.assertEqual(self.evidence(cib=cib, monitor=monitor, native_credential='current')['state'], 'protected')
        for status in ('expired', 'unknown'):
            result = self.evidence(cib=cib, monitor=monitor, native_credential=status)
            self.assertEqual(result['state'], 'degraded')
            self.assertEqual(result['owners'], 1)
            self.assertFalse(result['fencingReady'])
            self.assertEqual(result['localFencingCredential'], status)
        self.assertNotIn('localFencingCredential', self.evidence(native_credential='expired'))

    def test_local_expiry_reads_private_root_metadata_and_exports_only_state(self):
        with patch.object(cluster_status.cluster_setup, 'protected') as read:
            for version, expires, result in ((1, 101, 'current'), (1, 100, 'expired'),
                                             (1, True, 'unknown'), (True, 101, 'unknown')):
                read.return_value = dict(version=version, expires=expires, token='never exported')
                self.assertEqual(cluster_status.native_credential_status(now=100), result)
            read.side_effect = PermissionError
            self.assertEqual(cluster_status.native_credential_status(now=100), 'unknown')
            read.assert_called_with(cluster_status.FENCING_PROFILE, private=True)

    def test_owner_count_alone_cannot_confirm_remote_protection(self):
        for owner in ('', '<node name="unknown-node"/>'):
            with self.subTest(owner=owner):
                monitor = MONITOR.replace('<node name="node-a"/>', owner)
                self.assertEqual(self.evidence(monitor=monitor, local=False, node='node-b')['state'], 'degraded')
        self.assertEqual(self.evidence(local=False, node='unknown-node')['state'], 'degraded')

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

    def test_same_guest_cannot_cover_two_nodes_through_separate_fencers(self):
        root = xml(CIB)
        resources = root.find('configuration/resources')
        first = resources.find("primitive[@id='fencer']")
        first.find("instance_attributes/nvpair[@name='pcmk_host_list']").set('value', 'node-a')
        first.find("instance_attributes/nvpair[@name='pcmk_host_map']").set('value', 'node-a:vm-a')
        second = ET.fromstring(ET.tostring(first))
        second.set('id', 'second-fencer')
        second.find("instance_attributes/nvpair[@name='pcmk_host_list']").set('value', 'node-b')
        second.find("instance_attributes/nvpair[@name='pcmk_host_map']").set('value', 'node-b:vm-a')
        resources.append(second)
        self.assertEqual(fencing_coverage([first, second], {'fencer', 'second-fencer'}, {'node-a', 'node-b'}),
                         (False, False))
        second.find("instance_attributes/nvpair[@name='pcmk_host_map']").set('value', 'node-b:vm-b')
        self.assertEqual(fencing_coverage([first, second], {'fencer', 'second-fencer'}, {'node-a', 'node-b'}),
                         (True, True))

    def test_reported_agent_must_match_and_disabled_or_rule_driven_devices_are_not_proof(self):
        wrong = MONITOR.replace('resource_agent="stonith:fence_xvm"', 'resource_agent="stonith:fence_pve"')
        self.assertEqual(self.evidence(monitor=wrong)['state'], 'degraded')
        for metadata in ('<meta_attributes><nvpair name="target-role" value="Stopped"/></meta_attributes>',
                         '<meta_attributes><nvpair name="is-managed" value="false"/></meta_attributes>',
                         '<meta_attributes><rule/></meta_attributes>'):
            cib = CIB.replace('<primitive id="fencer" class="stonith" type="fence_xvm">',
                              '<primitive id="fencer" class="stonith" type="fence_xvm">' + metadata)
            self.assertFalse(self.evidence(cib=cib)['fencingConfigured'])

    def test_proxmox_fencing_requires_verified_tls_and_explicit_numeric_targets(self):
        cib = CIB.replace('fence_xvm', 'fence_pve').replace('node-a:vm-a;node-b:vm-b', 'node-a:101;node-b:102')
        cib = cib.replace('<instance_attributes>', '<instance_attributes>'
            '<nvpair name="ipaddr" value="pve.example.invalid"/><nvpair name="ssl" value="true"/>', 1)
        monitor = MONITOR.replace('fence_xvm', 'fence_pve')
        self.assertEqual(self.evidence(cib=cib, monitor=monitor)['state'], 'protected')
        self.assertEqual(self.evidence(cib=cib.replace('fence_pve', 'fence_proxypermit_pve'),
                                       monitor=monitor.replace('fence_pve', 'fence_proxypermit_pve'),
                                       native_credential='current')['state'], 'protected')
        for invalid in (cib.replace('name="ssl" value="true"', 'name="ssl" value="false"'),
                        cib.replace('name="ssl"', 'name="ssl_insecure"'),
                        cib.replace('node-b:102', 'node-b:named-guest'),
                        cib.replace('name="ipaddr"', 'name="unrecognized_endpoint"')):
            self.assertFalse(self.evidence(cib=invalid, monitor=monitor)['fencingConfigured'])

    def test_stopped_and_unknown_resources_are_distinct(self):
        monitor = MONITOR.replace('nodes_running_on="1"', 'nodes_running_on="0"').replace('<node name="node-a"/>', '')
        self.assertEqual(self.evidence(monitor=monitor, local=False)['state'], 'stopped')
        self.assertEqual(self.evidence(monitor='<crm_mon/>', local=False)['state'], 'unknown')

    def test_vmware_requires_https_with_verified_server_identity(self):
        cib = CIB.replace('fence_xvm', 'fence_vmware_rest').replace('<instance_attributes>', '<instance_attributes>'
            '<nvpair name="ipaddr" value="vcenter.example.invalid"/><nvpair name="ssl_secure" value="1"/>', 1)
        monitor = MONITOR.replace('fence_xvm', 'fence_vmware_rest')
        self.assertTrue(self.evidence(cib=cib, monitor=monitor)['fencingConfigured'])
        for insecure in ('ssl_insecure', 'ssl-insecure'):
            unsafe = cib.replace('</instance_attributes>', f'<nvpair name="{insecure}" value="TRUE"/></instance_attributes>', 1)
            self.assertFalse(self.evidence(cib=unsafe, monitor=monitor)['fencingConfigured'])

    def test_physical_hosts_require_one_encrypted_bmc_per_chassis(self):
        template = ('<primitive id="{id}" class="stonith" type="fence_ipmilan"><instance_attributes>'
            '<nvpair name="pcmk_host_check" value="static-list"/><nvpair name="pcmk_host_list" value="{node}"/>'
            '<nvpair name="pcmk_host_map" value="{node}:{node}"/><nvpair name="ipaddr" value="{endpoint}"/>'
            '<nvpair name="lanplus" value="1"/><nvpair name="cipher" value="17"/></instance_attributes></primitive>')
        fencers = ''.join(template.format(id='fence-' + side, node='node-' + side,
                                         endpoint='bmc-' + side + '.example.invalid') for side in ('a', 'b'))
        first = CIB.index('<primitive id="fencer"')
        last = CIB.index('</primitive>', first) + len('</primitive>')
        cib = CIB[:first] + fencers + CIB[last:]
        self.assertTrue(self.evidence(cib=cib)['fencingConfigured'])
        for unsafe in (cib.replace('bmc-b.', 'bmc-a.'), cib.replace('value="17"', 'value="0"'),
                       cib.replace('name="lanplus" value="1"', 'name="lanplus" value="0"')):
            self.assertFalse(self.evidence(cib=unsafe)['fencingConfigured'])

    def test_xml_entities_are_never_expanded(self):
        with self.assertRaises(ValueError):
            xml('<!DOCTYPE cib [<!ENTITY secret "private-fixture">]><cib>&secret;</cib>')
