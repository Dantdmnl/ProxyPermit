"""Native setup preserves existing policy and requires verified activation."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app.core import cluster_topology as topology
from scripts.deploy import cluster_native as native, cluster_preflight as preflight, cluster_witness as witness

PLAN = dict(version=1, platform='proxmox', witness='192.0.2.13', fenceAddress='192.0.2.100',
            nodes=[dict(name='node-a', address='192.0.2.11', target='101'),
                   dict(name='node-b', address='192.0.2.12', target='102')])


def empty_cib():
    return ET.fromstring('<cib><configuration><crm_config/><nodes/><resources/><constraints/></configuration></cib>')


class ClusterTopologyTests(unittest.TestCase):
    def test_power_observations_require_the_planned_peer_and_authenticate_off_before_on(self):
        credential = dict(host='pve.example', pveNode='host', target=102)
        identity = dict(profileHash='profile', caHash='ca', agentHash='agent', planHash='plan', target=102)
        address = [(None, None, None, None, ('192.0.2.100', 8006))]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ('a' * 32)
            folder.mkdir()
            pending = folder / 'fencing-off-observed.json'

            def read(path, **_options):
                return credential if path == preflight.CREDENTIAL else json.loads(path.read_text())

            with patch.object(preflight.cluster_setup, 'protected', side_effect=read), \
                    patch.object(preflight.install, 'require_trusted_path'), \
                    patch.object(preflight.socket, 'getaddrinfo', return_value=address), \
                    patch.object(native, 'fencing_identity', return_value=identity), \
                    patch.object(preflight.bootstrap, 'run') as run, patch.object(preflight.time, 'time', return_value=10000):
                with self.assertRaises(FileNotFoundError):
                    preflight.power_observation(PLAN, 'a', 'plan', folder, 'fence-on')
                run.assert_not_called()
                run.return_value = SimpleNamespace(returncode=8, stdout='Status: OFF')
                with self.assertRaisesRegex(ValueError, 'did not confirm'):
                    preflight.power_observation(PLAN, 'a', 'plan', folder, 'fence-off')
                self.assertFalse(pending.exists())
                run.return_value = SimpleNamespace(returncode=2, stdout='Status: OFF')
                (folder / 'fencing-tested.json').write_text('{}')
                preflight.power_observation(PLAN, 'a', 'plan', folder, 'fence-off')
                self.assertFalse((folder / 'fencing-tested.json').exists())
                self.assertEqual(run.call_args.args[-1], '--plug=102')
                self.assertNotIn('--action=off', run.call_args.args)
                pending.write_text(json.dumps(dict(**identity, checked=9000)))
                with self.assertRaisesRegex(ValueError, 'fresh power-off'):
                    preflight.power_observation(PLAN, 'a', 'plan', folder, 'fence-on')
                pending.write_text(json.dumps(dict(**identity, checked=9999)))
                run.return_value = SimpleNamespace(returncode=0, stdout='Status: ON')
                preflight.power_observation(PLAN, 'a', 'plan', folder, 'fence-on')
                self.assertFalse(pending.exists())
                self.assertTrue(json.loads((folder / 'fencing-tested.json').read_text())['recoveryConfirmed'])
                self.assertEqual(json.loads((folder.parent / 'preflight-current.json').read_text())['operation'], folder.name)
                with self.assertRaisesRegex(ValueError, 'limited to the other node'):
                    preflight.power_observation(PLAN, 'b', 'plan', folder, 'fence-off')

    def test_activation_receipt_rejects_changed_credentials_ca_agent_or_old_power_test(self):
        receipt = dict(profileHash='hash', caHash='hash', agentHash='hash', planHash='plan', target='102',
                       offConfirmed=True, recoveryConfirmed=True, checked=10000)
        with patch.object(native, 'digest', return_value='hash'), patch.object(native.time, 'time', return_value=10000):
            for changed in (None, 'profileHash', 'caHash', 'agentHash', 'target', 'planHash', 'offConfirmed', 'checked'):
                value = dict(receipt)
                if changed:value[changed] = 0 if changed=='checked' else 'different'
                with self.subTest(changed=changed), patch.object(native.cluster_setup, 'protected', side_effect=[value, dict(target='102', expires=20000)]):
                    if changed:
                        # checked=0 is still recent at time 10000; make it stale explicitly.
                        if changed=='checked':value['checked']=-100000
                        with self.assertRaises(ValueError):native.verify_power_receipt(Path('/fixture'), 'plan')
                    else:native.verify_power_receipt(Path('/fixture'), 'plan')
            with patch.object(native.cluster_setup, 'protected', side_effect=[receipt, dict(target='102', expires=10000)]):
                with self.assertRaisesRegex(ValueError, 'credential has expired'):
                    native.verify_power_receipt(Path('/fixture'), 'plan')

    def test_missing_native_tools_are_detected_before_reservation(self):
        prepared = dict(phase='ready', role='node', planHash='approved')
        with patch.object(native.cluster_setup, 'protected', return_value=prepared), \
             patch.object(native.bootstrap, 'plan'), patch.object(native.shutil, 'which', return_value=None), \
             patch.object(native.bootstrap, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'missing'):
                native.require_prepared('approved')
            run.assert_not_called()

    def test_peer_reservation_report_requires_correct_identity_and_fresh_observation(self):
        operation = 'a' * 32
        report = dict(operation=operation, planHash='plan', phase='reserved', vipPresent=False,
                      name='node-b', checked=10000)
        with patch.object(native.time, 'time', return_value=10000), \
                patch.object(native.cluster_setup, 'protected', return_value=report) as read:
            native.require_peer_reserved(Path('/fixture'), operation, 'plan', 'node-b')
            for changes in ({'checked': 9600}, {'checked': 10006}, {'checked': True}, {'checked': None},
                            {'vipPresent': True}, {'name': 'node-a'}, {'phase': 'transport'},
                            {'operation': 'b' * 32}, {'planHash': 'different'}):
                read.return_value = {**report, **changes}
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'fresh reservation report'):
                    native.require_peer_reserved(Path('/fixture'), operation, 'plan', 'node-b')

    def test_preflight_report_requires_both_certificate_and_key_fingerprints(self):
        operation = 'a' * 32
        ready = dict(version=1, cluster=topology.name(PLAN), planHash='plan',
                     caHash='b' * 64, certificateHash='c' * 64)
        with patch.object(native.cluster_setup, 'protected', return_value=ready) as read, \
                patch.object(native, 'private_bytes', return_value=b'k' * 1024), \
                patch.object(preflight, 'certificate_material', return_value='c' * 64), \
                patch.object(native.time, 'time', return_value=1000):
            proof = native.prepared_transport(PLAN, 'plan', Path('/fixture'))
            self.assertIsNotNone(proof)
            expected = dict(operation=operation, planHash='plan', phase='reserved', name='node-b',
                            vipPresent=False, checked=1000)
            read.return_value = {**expected, 'transportProof': proof}
            native.require_peer_reserved(Path('/fixture'), operation, 'plan', 'node-b', proof=proof)
            with self.assertRaisesRegex(ValueError, 'key differ'):
                native.require_peer_reserved(Path('/fixture'), operation, 'plan', 'node-b',
                                            proof={**proof, 'keyHash': 'd' * 64})
            read.return_value = expected
            with self.assertRaisesRegex(ValueError, 'key differ'):
                native.require_peer_reserved(Path('/fixture'), operation, 'plan', 'node-b', proof=proof)
            read.side_effect = FileNotFoundError
            with self.assertRaisesRegex(ValueError, 'Enroll and verify the witness'):
                native.prepared_transport(PLAN, 'plan', Path('/fixture'), required=True)

    def test_reservation_proof_checks_keepalived_and_vip_before_issuing_timestamp(self):
        local = dict(interface='ens18', address='192.0.2.10')
        with patch.object(native.bootstrap, 'run', return_value=SimpleNamespace(stdout='inactive\n')) as command, \
                patch.object(native.ha_resource, 'owns_vip', return_value=False) as owns, \
                patch.object(native.ha_resource, 'validate_service'), patch.object(native.time, 'time', return_value=10000):
            self.assertEqual(native.verify_reservation(local), 10000)
            owns.return_value = True
            with self.assertRaises(ValueError):
                native.verify_reservation(local)
            owns.return_value = False
            command.return_value = SimpleNamespace(stdout='active\n')
            with self.assertRaises(ValueError):
                native.verify_reservation(local)

    def test_interrupted_reservation_reports_live_vip_before_profile_exists(self):
        operation = 'a' * 32
        state = dict(phase='reserving', operation=operation, planHash='plan', name='node-a')
        local = dict(interface='ens18', vip='192.0.2.10/24')
        with patch.object(native, 'load', return_value=(PLAN, local, Path('/fixture'), state)), \
                patch.object(native.ha_resource, 'profile') as profile, \
                patch.object(native.ha_resource, 'owns_vip', return_value=True) as owns:
            report = native.status(operation)
            self.assertIs(report['vipPresent'], True)
            self.assertEqual(report['next'], 'inspect-reservation')
            owns.assert_called_once_with(dict(interface='ens18', address='192.0.2.10'))
            profile.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX reservation directory permissions.')
    def test_resume_refuses_stale_peer_or_changed_reservation_before_mutation(self):
        operation = 'a' * 32
        local = dict(interface='ens18', vip='192.0.2.10/24', node='a')
        checkpoint = dict(phase='reserving')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            state_path = path / 'checkpoint.json'
            state_path.touch()
            profile = path / 'profile.json'
            override = path / 'reservation.conf'
            with patch.object(native, 'STATE', state_path), patch.object(native.cluster, 'PROFILE', profile), \
                    patch.object(native.cluster, 'SERVICE_RESERVATION', override), \
                    patch.object(native, 'context', return_value=(PLAN, local, 'plan', path)), \
                    patch.object(native, 'load', return_value=(PLAN, local, path, checkpoint)), \
                    patch.object(native, 'require_prepared'), patch.object(native.install, 'require_trusted_path'), \
                    patch.object(native, 'prepared_transport', return_value=None), \
                    patch.object(native, 'verify_power_receipt'), \
                    patch.object(native.bootstrap, 'run', return_value=SimpleNamespace(stdout='inactive\n')) as command, \
                    patch.object(native.ha_resource, 'owns_vip', return_value=True) as owns, \
                    patch.object(native, 'require_peer_reserved', side_effect=ValueError('stale peer')) as peer, \
                    patch.object(native.cluster_setup, 'protected', return_value={'vip': 'different'}), \
                    patch('scripts.deploy.management_tls.protected', return_value='administrator-owned override'), \
                    patch.object(native, 'atomic_write') as write, patch.object(native.logging_policy, 'write_asset') as asset:
                with self.assertRaisesRegex(ValueError, 'stale peer'):
                    native.reserve(operation)
                peer.assert_called_once_with(path, operation, 'plan', 'node-b', proof=None)
                owns.return_value = False
                profile.touch()
                with self.assertRaisesRegex(ValueError, 'profile changed'):
                    native.reserve(operation)
                profile.unlink()
                override.touch()
                with self.assertRaisesRegex(ValueError, 'override changed'):
                    native.reserve(operation)
                write.assert_not_called()
                asset.assert_not_called()
                self.assertTrue(all(call.args[1] == 'show' for call in command.call_args_list))

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

    def test_explicit_retry_revalidates_the_stopped_policy_and_fencing(self):
        state = dict(phase='activating', planHash='plan')
        safe = dict(owners=0, quorate=True, witnessConnected=True, fencingConfigured=True, fencingReady=True, policySafe=True)
        with patch.object(native, 'load', return_value=(PLAN, {}, Path('/fixture'), state)), \
                patch.object(native.bootstrap, 'plan'), \
                patch.object(native.cluster_setup, 'protected', return_value=dict(host='pve.example', pveNode='pve')), \
                patch.object(native, 'verify_power_receipt') as receipt, \
                patch.object(native.cluster_maintenance, 'query') as query, \
                patch.object(native.cluster_status, 'evidence', return_value=safe), \
                patch.object(native, 'save'), patch.object(native, 'audit') as audit, \
                patch.object(native.cluster_maintenance, 'replace', return_value=False) as replace:
            for scenario in ('started', 'reserved', 'missing-witness', 'stopped'):
                with self.subTest(scenario=scenario):
                    cib = topology.initial_configuration(empty_cib(), PLAN, 'pve.example', 'pve')
                    if scenario == 'started':
                        cib.find(".//nvpair[@name='target-role']").set('value', 'Started')
                    if scenario == 'reserved':
                        ET.SubElement(cib.find('configuration/nodes'), 'instance_attributes', id=native.cluster_lease.LEASE_ID)
                    query.return_value = cib
                    safe['witnessConnected'] = scenario != 'missing-witness'
                    with self.assertRaises(ValueError):
                        native.activate('a' * 32, retry=True)
                    self.assertEqual(replace.call_count, int(scenario == 'stopped'))
            self.assertEqual(receipt.call_count, 4)
            audit.assert_called_once_with('cluster_setup_activation_retried', 'a' * 32)

    def test_read_only_activation_status_distinguishes_a_pending_write_from_completion(self):
        operation = 'a' * 32
        state = dict(phase='activating', planHash='plan', operation=operation, name='node-a',
                     vipPresent=False, keyHash='private-key-hash')
        evidence = dict(state='stopped', owners=0, quorate=True, witnessConnected=True, fencingConfigured=True,
                        fencingReady=True, policySafe=True)
        cib = topology.initial_configuration(empty_cib(), PLAN, 'pve.example', 'pve')
        with patch.object(native, 'load', return_value=(PLAN, {}, Path('/fixture'), state)), \
                patch.object(native.cluster_setup, 'protected', side_effect=[state, dict(host='pve.example', pveNode='pve')]), \
                patch.object(native.ha_resource, 'profile'), patch.object(native.ha_resource, 'owns_vip', return_value=False), \
                patch.object(native.cluster_maintenance, 'query', return_value=cib), \
                patch.object(native.cluster_status, 'evidence', return_value=evidence), \
                patch.object(native, 'verify_power_receipt'), \
                patch.object(native.cluster_maintenance, 'replace') as replace, patch.object(native, 'save') as save, \
                patch.object(native.install, 'require_linux_root'), patch.object(native.install, 'maintenance_lock') as lock, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(native.main(['--phase', 'status']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['next'], 'retry-activation')
            self.assertNotIn('keyHash', report)
            replace.assert_not_called(); save.assert_not_called(); lock.assert_not_called()
        cib.find(".//nvpair[@name='target-role']").set('value', 'Started')
        with patch.object(native, 'load', return_value=(PLAN, {}, Path('/fixture'), state)), \
                patch.object(native.cluster_setup, 'protected', return_value=dict(host='pve.example', pveNode='pve')), \
                patch.object(native.ha_resource, 'profile'), patch.object(native.ha_resource, 'owns_vip', return_value=True), \
                patch.object(native.cluster_maintenance, 'query', return_value=cib), \
                patch.object(native.cluster_status, 'evidence', return_value={'state': 'protected', 'owners': 1}):
            self.assertEqual(native.status(operation)['next'], 'finalize')
            cib.find('configuration/crm_config').append(ET.fromstring(
                '<cluster_property_set id="cib-bootstrap-options"><nvpair name="last-lrm-refresh" value="1763834400"/></cluster_property_set>'))
            self.assertEqual(native.status(operation)['next'], 'finalize')
            cib.find('configuration/crm_config').append(ET.fromstring(
                '<cluster_property_set id="operator"><nvpair name="maintenance-mode" value="true"/></cluster_property_set>'))
            self.assertEqual(native.status(operation)['next'], 'review')

    def test_witness_requires_mutual_tls_and_rotates_only_private_namespace(self):
        assets = witness.assets(PLAN)
        service = assets[witness.DROPIN]
        self.assertIn('-s req -c on', service)
        self.assertIn('-l 192.0.2.13', service)
        self.assertIn('ProtectSystem=strict', service)
        retention = next(text for path,text in assets.items() if path.name=='proxypermit-witness-retention.service')
        self.assertIn('--namespace=proxypermit --rotate --vacuum-time=30d --vacuum-size=100M', retention)
