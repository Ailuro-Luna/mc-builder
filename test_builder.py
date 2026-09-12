import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import builder
import mcp_server
from rcon import Rcon, properties


class BlueprintTests(unittest.TestCase):
    def setUp(self):
        self.cfg=builder.config()
        self.plan={'dimension':'minecraft:overworld','operations':[
            {'from':[1,100,1],'to':[3,102,3],'block':'minecraft:stone','hollow':True}]}

    def test_hollow_and_order(self):
        p=builder.compile_plan(self.plan,self.cfg)
        self.assertEqual(len(p['blocks']),26)
        self.assertNotIn([2,101,2],[b['pos'] for b in p['blocks']])
        self.assertEqual([b['pos'][1] for b in p['blocks']],sorted(b['pos'][1] for b in p['blocks']))

    def test_overlapping_operations_have_single_final_target(self):
        self.plan['operations'].append({'from':[1,100,1],'block':'minecraft:glass'})
        p=builder.compile_plan(self.plan,self.cfg)
        self.assertEqual(len(p['blocks']),26)
        self.assertEqual(p['blocks'][0]['target'],'minecraft:glass')

    def test_rejects_unconfigured_dimension(self):
        self.plan['dimension']='unknown:unconfigured'
        with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_rejects_reversed_or_excessive_cuboids(self):
        for coords in ([0,102,3],[100000,102,3]):
            self.plan['operations'][0]['to']=coords
            with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_rejects_boolean_coordinate(self):
        self.plan['operations'][0]['from'][0]=True
        with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_rejects_unapproved_material_and_nbt(self):
        for material in ('minecraft:tnt','minecraft:stone{test:1}','create:deployer'):
            self.plan['operations'][0]['block']=material
            with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_combined_extent_not_just_each_operation(self):
        self.plan['operations'].append({'from':[100,100,100],'block':'minecraft:stone'})
        with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_write_gate_and_area_boundary(self):
        plan=builder.compile_plan(self.plan,self.cfg)
        with patch.object(builder,'config',return_value=self.cfg):
            with self.assertRaises(ValueError):builder.approved(plan)
        cfg=copy.deepcopy(self.cfg)
        cfg.update(allow_write=True,approved_area={'dimension':'minecraft:overworld','min':[0,99,0],'max':[2,103,4]})
        with patch.object(builder,'config',return_value=cfg):
            with self.assertRaises(ValueError):builder.approved(plan)

    def test_blueprint_cannot_read_files_outside_plans(self):
        with self.assertRaises(ValueError):builder.preview_build('/etc/passwd')

    def test_unknown_or_path_traversal_job_rejected(self):
        for jid in ('../../etc','a'*24):
            with self.assertRaises(ValueError):builder.job_path(jid)

    def test_source_water_is_explicit_and_preserved_in_plan(self):
        plain=builder.compile_plan(self.plan,self.cfg)
        self.assertNotIn('replace_source_water',plain)
        self.plan['replace_source_water']=True
        self.assertTrue(builder.compile_plan(self.plan,self.cfg)['replace_source_water'])
        self.plan['replace_source_water']='yes'
        with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_replacement_does_not_allow_flowing_water_or_solids(self):
        old={'loaded':True,'pos':[1,100,1],'name':'water','state':{'level':'0'},'has_block_entity':False}
        self.assertFalse(builder.replaceable(old,{}))
        self.assertTrue(builder.replaceable(old,{'replace_source_water':True}))
        for changed in ({'state':{'level':'1'}},{'name':'dirt','state':{}},{'loaded':False},{'has_block_entity':True}):
            self.assertFalse(builder.replaceable({**old,**changed},{'replace_source_water':True}))

    def test_source_water_needs_site_gate_and_path_is_protected(self):
        self.plan['replace_source_water']=True
        plan=builder.compile_plan(self.plan,self.cfg)
        cfg={**self.cfg,'allow_write':True,'approved_area':{'dimension':'minecraft:overworld','min':[0,99,0],'max':[4,104,4]}}
        with patch.object(builder,'config',return_value=cfg):
            with self.assertRaisesRegex(ValueError,'Source-water'):builder.approved(plan)
            cfg['allow_source_water']=True
            builder.approved(plan)
            cfg['protected_columns']={'1,1':[99,104]}
            with self.assertRaisesRegex(ValueError,'protected'):builder.approved(plan)
            cfg['protected_columns']={'1,1':[80,90]}
            builder.approved(plan)

    def test_target_state_is_validated_and_compiled(self):
        self.plan['operations'][0]['block']='minecraft:stone'
        self.plan['operations'][0]['state']={'axis':'y'}
        compiled=builder.compile_plan(self.plan,self.cfg)
        self.assertTrue(all(b['target_state']=={'axis':'y'} for b in compiled['blocks']))
        for state in ({'bad key':'x'},{'axis':1},{'axis':'UP'}):
            self.plan['operations'][0]['state']=state
            with self.assertRaises(ValueError):builder.compile_plan(self.plan,self.cfg)

    def test_existing_replacement_needs_plan_and_site_gates(self):
        old={'loaded':True,'pos':[1,100,1],'name':'stone','state':{},'has_block_entity':False}
        self.assertFalse(builder.replaceable(old,{}))
        self.assertTrue(builder.replaceable(old,{'replace_blocks':['stone']}))
        self.assertFalse(builder.replaceable({**old,'state':{'axis':'x'}},{'replace_blocks':['stone']}))
        self.plan['replace_blocks']=['minecraft:stone']
        plan=builder.compile_plan(self.plan,self.cfg)
        self.assertEqual(plan['replace_blocks'],['stone'])
        cfg={**self.cfg,'allow_write':True,'approved_area':{'dimension':'minecraft:overworld','min':[0,99,0],'max':[4,104,4]}}
        with patch.object(builder,'config',return_value=cfg):
            with self.assertRaisesRegex(ValueError,'Existing-block'):builder.approved(plan)
            cfg['allow_replace_blocks']=['stone']
            builder.approved(plan)


def packet(rid,kind,text):
    data=struct.pack('<ii',rid,kind)+text.encode()+b'\0\0'
    return struct.pack('<i',len(data))+data


class FakeSocket:
    def __init__(self,data):self.data=data;self.sent=[];self.closed=False
    def recv(self,n):
        take=min(n,3)
        value=self.data[:take];self.data=self.data[take:];return value
    def sendall(self,data):self.sent.append(data)
    def close(self):self.closed=True


class TransportTests(unittest.TestCase):
    def test_fragmented_and_multiple_response_packets(self):
        s=FakeSocket(packet(1,2,'')+packet(2,0,'hello ')+packet(2,0,'world')+packet(3,0,'Unknown request 0'))
        with patch('rcon.properties',return_value={'enable-rcon':'true','rcon.port':'25575','rcon.password':'test-only'}),patch('rcon.socket.create_connection',return_value=s):
            with Rcon() as r:self.assertEqual(r.command('list'),'hello world')
        self.assertTrue(s.closed)

    def test_auth_rejection_does_not_leak_secret(self):
        s=FakeSocket(packet(-1,2,''))
        with patch('rcon.properties',return_value={'enable-rcon':'true','rcon.port':'25575','rcon.password':'test-only'}),patch('rcon.socket.create_connection',return_value=s):
            with self.assertRaises(RuntimeError) as ctx:Rcon()
        self.assertNotIn('test-only',str(ctx.exception))
        self.assertTrue(s.closed)

    def test_properties_java_escaping(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'props'
            p.write_text('rcon.password=ab\\=cd\\u0021\nkey = value\ncontinued=ab\\\n cd\n')
            props=properties(p)
        self.assertEqual(props['rcon.password'],'ab=cd!')
        self.assertEqual(props['key'],'value')
        self.assertEqual(props['continued'],'abcd')


class ProtocolTests(unittest.TestCase):
    def test_initialization_and_tool_listing(self):
        response,ready=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},False)
        self.assertTrue(ready)
        self.assertEqual(response['result']['protocolVersion'],'2025-06-18')
        response,_=mcp_server.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'},ready)
        names={t['name'] for t in response['result']['tools']}
        self.assertIn('server_status',names)
        self.assertNotIn('execute_rcon',names)

    def test_unknown_tool_and_bad_arguments(self):
        response,_=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'stop'}},True)
        self.assertIn('error',response)
        response,_=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'search_blocks','arguments':{'query':'stone','extra':1}}},True)
        self.assertTrue(response['result']['isError'])


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.cfg=copy.deepcopy(builder.config())
        self.cfg.update(allow_write=True,approved_area={'dimension':'minecraft:overworld','min':[0,100,0],'max':[2,102,2]},shared_root=str(self.base/'shared'))
        self.patches=[patch.object(builder,'BASE',self.base),patch.object(builder,'config',return_value=self.cfg),
                      patch.object(builder,'health',return_value={'epoch':'test','mean_mspt':10})]
        for p in self.patches:p.start()
        self.jid='a'*24
        self.root=self.base/'jobs'/self.jid
        self.blocks=[{'pos':[0,100,0],'target':'minecraft:stone'},{'pos':[1,100,0],'target':'minecraft:stone'}]
        self.rows=[{'loaded':True,'pos':x['pos'],'name':'air','state':{},'has_block_entity':False} for x in self.blocks]
        plan={'dimension':'minecraft:overworld','min':[0,100,0],'max':[1,100,0],'blocks':self.blocks}
        builder.atomic(self.root/'plan.json',plan)
        builder.atomic(self.root/'snapshot.json',self.rows)
        builder.atomic(self.root/'status.json',{'job_id':self.jid,'state':'starting','completed':0,'total':2,'prepared_epoch':'test',
            'digest':hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()})

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_partial_batches_continue_without_duplicating_prefix(self):
        visited=[]
        def fake(action,request_id,**params):
            visited.append(params['operations'][0]['pos'])
            builder.atomic(Path(self.cfg['shared_root'])/'results'/(request_id+'.json'),{'ok':True,'applied':1})
            return {'ok':True}
        with patch.object(builder,'request',side_effect=fake):builder.run_job(self.jid)
        state=builder.build_status(self.jid)
        self.assertEqual(state['state'],'completed')
        self.assertEqual(visited,[[0,100,0],[1,100,0]])
        self.assertNotIn('pending_request',state)

    def test_changed_blueprint_is_rejected(self):
        plan=json.loads((self.root/'plan.json').read_text())
        plan['blocks'][0]['target']='minecraft:glass'
        builder.atomic(self.root/'plan.json',plan)
        with self.assertRaises(ValueError):builder.load_plan(self.root)

    def test_uncertain_batch_keeps_reconciliation_marker(self):
        def fake(action,request_id,**params):
            builder.atomic(Path(self.cfg['shared_root'])/'results'/(request_id+'.json'),{'ok':False,'applied':0,'uncertain':True})
        with patch.object(builder,'request',side_effect=fake):builder.run_job(self.jid)
        state=builder.build_status(self.jid)
        self.assertEqual(state['state'],'paused')
        self.assertIn('pending_request',state)
        with self.assertRaises(ValueError):builder.rollback_build(self.jid)

    def test_cancel_before_next_batch_does_not_place(self):
        (self.root/'pause').touch();(self.root/'cancel').touch()
        with patch.object(builder,'request') as request:
            builder.run_job(self.jid);request.assert_not_called()
        self.assertEqual(builder.build_status(self.jid)['state'],'cancelled')

    def test_worker_reload_pauses_before_writing(self):
        with patch.object(builder,'health',return_value={'epoch':'changed','mean_mspt':10}),patch.object(builder,'request') as request:
            builder.run_job(self.jid);request.assert_not_called()
        self.assertIn('restarted',builder.build_status(self.jid)['error'])

    def test_rollback_keeps_player_changes(self):
        state=builder.read_job_state(self.jid)
        state.update(completed=2,rollback_cursor=1,rollback_epoch='test',rollback_conflicts=[])
        builder.atomic(self.root/'status.json',state)
        current={tuple(self.rows[0]['pos']):{**self.rows[0],'name':'diamond_block'},
                 tuple(self.rows[1]['pos']):{**self.rows[1],'name':'stone'}}
        restored=[]
        def fake(action,request_id,**params):
            restored.append(params['operations'][0]['pos'])
            builder.atomic(Path(self.cfg['shared_root'])/'results'/(request_id+'.json'),{'ok':True,'applied':1})
        with patch.object(builder,'read_positions',side_effect=lambda dim,positions:[current[tuple(p)] for p in positions]),patch.object(builder,'request',side_effect=fake):
            builder.rollback_job(self.jid)
        state=builder.build_status(self.jid)
        self.assertEqual(restored,[[1,100,0]])
        self.assertEqual(state['state'],'rolled_back_with_conflicts')
        self.assertEqual(state['rollback_conflict_count'],1)

    def test_water_rollback_preserves_original_source_state(self):
        self.cfg['allow_source_water']=True
        plan=json.loads((self.root/'plan.json').read_text())
        plan['replace_source_water']=True
        builder.atomic(self.root/'plan.json',plan)
        before=[{**r,'name':'water','state':{'level':'0'}} for r in self.rows]
        builder.atomic(self.root/'snapshot.json',before)
        state=builder.read_job_state(self.jid)
        state.update(completed=2,rollback_cursor=1,rollback_epoch='test',rollback_conflicts=[],
                     digest=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest())
        builder.atomic(self.root/'status.json',state)
        requests=[]
        def fake(action,request_id,**params):
            requests.append(params)
            builder.atomic(Path(self.cfg['shared_root'])/'results'/(request_id+'.json'),{'ok':True,'applied':1})
        current={tuple(r['pos']):{**r,'name':'stone','state':{}} for r in self.rows}
        with patch.object(builder,'read_positions',side_effect=lambda dim,positions:[current[tuple(p)] for p in positions]),patch.object(builder,'request',side_effect=fake):
            builder.rollback_job(self.jid)
        self.assertEqual(builder.build_status(self.jid)['state'],'rolled_back')
        self.assertEqual(len(requests),2)
        for params in requests:
            self.assertTrue(params['replace_source_water'])
            self.assertEqual(params['operations'][0]['target'],'water')
            self.assertEqual(params['operations'][0]['restore_state'],{'level':'0'})

    def test_stateful_target_is_sent_and_used_for_rollback_comparison(self):
        plan=json.loads((self.root/'plan.json').read_text())
        plan['blocks'][0]['target']='minecraft:stone'
        plan['blocks'][0]['target_state']={'axis':'y'}
        builder.atomic(self.root/'plan.json',plan)
        state=builder.read_job_state(self.jid)
        state['total']=1
        state['digest']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
        builder.atomic(self.root/'status.json',state)
        sent=[]
        def fake(action,request_id,**params):
            sent.append(params)
            builder.atomic(Path(self.cfg['shared_root'])/'results'/(request_id+'.json'),{'ok':True,'applied':1})
            return {'ok':True}
        with patch.object(builder,'request',side_effect=fake):builder.run_job(self.jid)
        self.assertEqual(sent[0]['operations'][0]['target_state'],{'axis':'y'})
        state=builder.read_job_state(self.jid)
        state.update(state='completed',completed=1,rollback_cursor=0,rollback_epoch='test',rollback_conflicts=[])
        builder.atomic(self.root/'status.json',state)
        current={**self.rows[0],'name':'stone','state':{'axis':'y'}}
        sent.clear()
        with patch.object(builder,'read_positions',return_value=[current]),patch.object(builder,'request',side_effect=fake):
            builder.rollback_job(self.jid)
        self.assertEqual(builder.build_status(self.jid)['state'],'rolled_back')


if __name__=='__main__':unittest.main()
