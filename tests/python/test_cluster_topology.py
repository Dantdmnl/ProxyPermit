"""Native setup preserves existing policy and requires verified activation."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app.core import cluster_topology as topology
from scripts.deploy import cluster_native as native, cluster_witness as witness

PLAN = dict(version=1, platform='proxmox', witness='192.0.2.13', fenceAddress='192.0.2.100',
            nodes=[dict(name='node-a', address='192.0.2.11', target='101'),
                   dict(name='node-b', address='192.0.2.12', target='102')])


def empty_cib():
    return ET.fromstring('<cib><configuration><crm_config/><nodes/><resources/><constraints/></configuration></cib>')


class ClusterTopologyTests(unittest.TestCase):
    def test_activation_receipt_rejects_changed_credentials_ca_agent_or_old_power_test(self):
        receipt = dict(profileHash='hash', caHash='hash', agentHash='hash', planHash='plan', target='102',
                       offConfirmed=True, recoveryConfirmed=True, checked=10000)
        with patch.object(native, 'digest', return_value='hash'), patch.object(native.time, 'time', return_value=10000):
            for changed in (None, 'profileHash', 'caHash', 'agentHash', 'target', 'planHash', 'offConfirmed', 'checked'):
                value = dict(receipt)
                if changed:value[changed] = 0 if changed=='checked' else 'different'
                with self.subTest(changed=changed), patch.object(native.cluster_setup, 'protected', side_effect=[value, dict(target='102')]):
                    if changed:
                        # checked=0 is still recent at time 10000; make it stale explicitly.
                        if changed=='checked':value['checked']=-100000
                        with self.assertRaises(ValueError):native.verify_power_receipt(Path('/fixture'), 'plan')
                    else:native.verify_power_receipt(Path('/fixture'), 'plan')

    def test_missing_native_tools_are_detected_before_reservation(self):
        prepared = dict(phase='ready', role='node', planHash='approved')
        with patch.object(native.cluster_setup, 'protected', return_value=prepared), \
             patch.object(native.bootstrap, 'plan'), patch.object(native.shutil, 'which', return_value=None), \
             patch.object(native.bootstrap, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'missing'):
                native.require_prepared('approved')
            run.assert_not_called()

    def test_unprivileged_audit_uses_readable_installed_release(self):
        with patch.object(native.bootstrap, 'run') as run:
            native.audit('cluster_setup_transport_started', 'a'*32)
        self.assertEqual(run.call_args.kwargs['cwd'], Path('/opt/proxypermit/current'))
        self.assertIn('scripts.tools.record_maintenance', run.call_args.args)

    def test_binary_authkey_is_preserved_without_text_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'authkey'
            value = bytes(range(256))*4
            native.write_key(path, value)
            self.assertEqual(path.read_bytes(), value)
            if os.name != 'nt':self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_transport_requires_encryption_and_three_votes(self):
        config = topology.corosync_config(PLAN)
        for expected in ('two_node: 0', 'expected_votes: 3', 'wait_for_all: 1', 'tls: required',
                         'crypto_cipher: aes256', 'crypto_hash: sha256', 'algorithm: ffsplit'):
            self.assertIn(expected, config)
        other = copy.deepcopy(PLAN);other['nodes'][1]['target'] = '103'
        self.assertNotEqual(topology.name(PLAN), topology.name(other))

    def test_firewall_is_directional_and_limits_services_to_exact_peers(self):
        rules = topology.firewall_rules(PLAN, 'a', 'ens18')
        self.assertEqual([row[row.index('port')+1] for row in rules], ['5405','5405','5403','8006'])
        self.assertEqual([row[1] for row in rules], ['in','out','out','out'])
        self.assertTrue(all('192.0.2.11' in row for row in rules))
        self.assertNotIn('2224', str(rules))
        self.assertNotIn('0.0.0.0/0', str(rules))
        with self.assertRaises(ValueError):topology.firewall_rules(PLAN, 'a', 'ens18;command')
        witness_rules = topology.firewall_rules(PLAN, 'witness', 'ens18')
        self.assertEqual(len(witness_rules), 2)
        self.assertTrue(all(row[1]=='in' and '5403' in row and PLAN['witness'] in row for row in witness_rules))

    def test_initial_cib_stops_vip_requires_fencing_and_contains_no_credentials(self):
        cib = topology.initial_configuration(empty_cib(), PLAN, 'pve.example', 'pve')
        self.assertTrue(native.configuration_matches(cib, PLAN, 'pve.example', 'pve'))
        pairs = {p.get('name'):p.get('value') for p in cib.iter('nvpair')}
        self.assertEqual(pairs['target-role'], 'Stopped')
        self.assertEqual(pairs['requires'], 'fencing')
        self.assertEqual(pairs['no-quorum-policy'], 'stop')
        self.assertEqual(pairs['stonith-action'], 'off')
        self.assertEqual(pairs['pcmk_host_map'], 'node-a:101;node-b:102')
        self.assertFalse(any('password' in key or 'token' in key for key in pairs))
        cib.find("configuration/resources/primitive[@id='proxypermit-vip']/meta_attributes/nvpair[@name='requires']").set('value','nothing')
        self.assertFalse(native.configuration_matches(cib, PLAN, 'pve.example', 'pve'))

    def test_existing_resources_constraints_and_admin_policy_are_never_adopted(self):
        for section, xml in [('resources','<primitive id="foreign"/>'),
                             ('constraints','<rsc_location id="foreign"/>'),
                             ('crm_config','<cluster_property_set><nvpair name="stonith-enabled" value="false"/></cluster_property_set>')]:
            cib = empty_cib();cib.find('configuration/'+section).append(ET.fromstring(xml))
            before = ET.tostring(cib)
            with self.assertRaises(ValueError):topology.initial_configuration(cib, PLAN, 'pve.example', 'pve')
            self.assertEqual(ET.tostring(cib), before)

    def test_activation_uncertain_write_retains_checkpoint_and_never_reissues(self):
        cib = topology.initial_configuration(empty_cib(), PLAN, 'pve.example', 'pve')
        state = dict(phase='configured', planHash='plan');saved=[]
        safe = dict(owners=0, quorate=True, witnessConnected=True, fencingConfigured=True, fencingReady=True, policySafe=True)
        with patch.object(native,'load',return_value=(PLAN, {}, None, state)), \
             patch.object(native.bootstrap,'plan'), patch.object(native.cluster_setup,'protected',return_value=dict(host='pve.example',pveNode='pve')), \
             patch.object(native,'verify_power_receipt'), \
             patch.object(native.cluster_maintenance,'query',return_value=cib), \
             patch.object(native.cluster_status,'evidence',return_value=safe), \
             patch.object(native,'save',side_effect=lambda value:saved.append(dict(value))), \
             patch.object(native,'audit'), patch.object(native.cluster_maintenance,'replace',return_value=False) as replace:
            with self.assertRaises(ValueError):native.activate('a'*32)
            self.assertEqual(saved[-1]['phase'],'activating')
            replace.assert_called_once()
            with patch.object(native.time,'monotonic',side_effect=[0,61]):
                with self.assertRaises(ValueError):native.activate('a'*32)
            replace.assert_called_once()

    def test_no_activation_when_peer_or_fencer_is_unverified(self):
        cib = topology.initial_configuration(empty_cib(), PLAN, 'pve.example', 'pve')
        for failed in ('quorate','witnessConnected','fencingConfigured','fencingReady','policySafe'):
            evidence=dict(owners=0,quorate=True,witnessConnected=True,fencingConfigured=True,fencingReady=True,policySafe=True)
            evidence[failed]=False
            with self.subTest(failed=failed), patch.object(native,'load',return_value=(PLAN,{},None,dict(phase='configured',planHash='plan'))), \
                 patch.object(native.bootstrap,'plan'), patch.object(native.cluster_setup,'protected',return_value=dict(host='pve.example',pveNode='pve')), \
                 patch.object(native,'verify_power_receipt'), \
                 patch.object(native.cluster_maintenance,'query',return_value=cib), patch.object(native.cluster_status,'evidence',return_value=evidence), \
                 patch.object(native.cluster_maintenance,'replace') as replace, patch.object(native,'save') as save:
                with self.assertRaises(ValueError):native.activate('a'*32)
                replace.assert_not_called();save.assert_not_called()

    def test_witness_requires_mutual_tls_and_rotates_only_private_namespace(self):
        assets = witness.assets(PLAN)
        service = assets[witness.DROPIN]
        self.assertIn('-s req -c on', service)
        self.assertIn('-l 192.0.2.13', service)
        self.assertIn('ProtectSystem=strict', service)
        retention = next(text for path,text in assets.items() if path.name=='proxypermit-witness-retention.service')
        self.assertIn('--namespace=proxypermit --rotate --vacuum-time=30d --vacuum-size=100M', retention)
