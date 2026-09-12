import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import builder
import configure_dimensions
import dimensions
import mcp_server
import set_area


def example_config():
    return json.loads((Path(__file__).parent/'config.example.json').read_text())


class DimensionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = example_config()

    def test_legacy_config_keeps_old_height_and_overworld_only(self):
        self.cfg.pop('dimensions')
        self.assertEqual(dimensions.position([0,319,0], dimensions.OVERWORLD, self.cfg), [0,319,0])
        for dim, y in [('minecraft:the_end',100), (dimensions.OVERWORLD,320)]:
            with self.assertRaises(ValueError):
                dimensions.position([0,y,0],dim,self.cfg)

    def test_height_edges_are_dimension_specific(self):
        self.cfg['dimensions']['minecraft:the_nether']['enabled'] = True
        for dim, low, high in [(dimensions.OVERWORLD,-64,319),('minecraft:the_end',0,255),('minecraft:the_nether',0,255)]:
            for y in [low,high]:
                dimensions.position([0,y,0],dim,self.cfg)
            for y in [low-1,high+1]:
                with self.assertRaises(ValueError):dimensions.position([0,y,0],dim,self.cfg)

    def test_custom_live_heights_and_disabled_unknown_dimensions(self):
        live = [{'dimension':'minecraft:the_end','min_y':0,'max_y':1023},
                {'dimension':'example:custom','min_y':-64,'max_y':1023}]
        cfg = dimensions.configure(self.cfg,live,['minecraft:the_end','example:custom'])
        for dim in ['minecraft:the_end','example:custom']:
            dimensions.position([0,1023,0],dim,cfg)
            with self.assertRaises(ValueError):dimensions.position([0,1024,0],dim,cfg)
        for dim in ['unknown:world','the_end','']:
            with self.assertRaises(ValueError):dimensions.specification(dim,cfg)
        cfg['dimensions']['minecraft:the_end']['enabled'] = False
        with self.assertRaises(ValueError):dimensions.position([0,100,0],'minecraft:the_end',cfg)

    def test_invalid_height_and_boolean_positions(self):
        for span in [(True,255),(0,False),(100,99),(0.5,255)]:
            self.cfg['dimensions']['minecraft:the_end'].update(min_y=span[0],max_y=span[1])
            with self.assertRaises(ValueError):dimensions.specification('minecraft:the_end',self.cfg)
        with self.assertRaises(ValueError):dimensions.position([True,100,0],dimensions.OVERWORLD,self.cfg)

    def test_migration_preserves_protection_and_closes_every_gate(self):
        self.cfg.update(allow_write=True,approved_area={'dimension':dimensions.OVERWORLD},
                        allow_source_water=True,allow_replace_blocks=['stone'],
                        protected_columns={'1,2':[61,66]})
        self.cfg['protected_columns_by_dimension'] = {dimensions.OVERWORLD:{'1,2':[60,64]},'minecraft:the_end':{'8,9':[3,8]}}
        before = copy.deepcopy(self.cfg)
        cfg = dimensions.configure(self.cfg,[{'dimension':'minecraft:the_end','min_y':0,'max_y':1023}],['minecraft:the_end'])
        self.assertEqual(self.cfg,before)
        self.assertEqual(cfg['protected_columns'],before['protected_columns'])
        self.assertEqual(cfg['protected_columns_by_dimension'][dimensions.OVERWORLD]['1,2'],[60,66])
        self.assertEqual(cfg['protected_columns_by_dimension']['minecraft:the_end'],{'8,9':[3,8]})
        self.assertFalse(cfg['allow_write']);self.assertFalse(cfg['allow_source_water'])
        self.assertIsNone(cfg['approved_area']);self.assertEqual(cfg['allow_replace_blocks'],[])
        with self.assertRaises(ValueError):dimensions.configure(cfg,[],['unknown:world'])

    def test_same_coordinates_in_different_worlds_do_not_share_protection(self):
        self.cfg['protected_columns'] = {'1,2':[60,66]}
        self.cfg['protected_columns_by_dimension'] = {'minecraft:the_end':{'1,2':[70,76]}}
        self.assertTrue(dimensions.protected([1,63,2],dimensions.OVERWORLD,self.cfg))
        self.assertFalse(dimensions.protected([1,63,2],'minecraft:the_end',self.cfg))
        self.assertTrue(dimensions.protected([1,73,2],'minecraft:the_end',self.cfg))
        self.assertFalse(dimensions.protected([1,73,2],dimensions.OVERWORLD,self.cfg))

    def test_snapshot_margin_clips_at_each_dimension_boundary(self):
        for dim, y, expected in [('minecraft:the_end',0,(0,2)),('minecraft:the_end',255,(253,255)),(dimensions.OVERWORLD,-64,(-64,-62))]:
            lo,hi = dimensions.snapshot_bounds({'dimension':dim,'min':[0,y,0],'max':[0,y,0]},self.cfg)
            self.assertEqual((lo[1],hi[1]),expected)
            self.assertEqual((lo[0],hi[0]),(-2,2))

    def test_read_validates_height_before_sending_and_keeps_dimension(self):
        with patch.object(builder,'config',return_value=self.cfg),patch.object(builder,'request',return_value={'blocks':[]}) as send:
            builder.read_positions('minecraft:the_end',[[0,100,0]])
            send.assert_called_once_with('read',dimension='minecraft:the_end',positions=[[0,100,0]])
            send.reset_mock()
            with self.assertRaises(ValueError):builder.read_positions('minecraft:the_end',[[0,-1,0]])
            send.assert_not_called()

    def test_mcp_accepts_namespaced_dimensions_and_defers_policy_to_builder(self):
        schema=mcp_server.TOOLS['read_blocks'][1]['inputSchema']['properties']['dim']
        self.assertNotIn('enum',schema)
        with patch.object(builder,'config',return_value=self.cfg),patch.object(builder,'request',return_value={'blocks':[]}) as send:
            response,_=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                'name':'read_blocks','arguments':{'dim':'minecraft:the_end','positions':[[0,100,0]]}}},True)
            self.assertFalse(response['result']['isError'])
            self.assertEqual(send.call_args.kwargs['dimension'],'minecraft:the_end')

    def test_water_permissions_are_not_inherited_by_new_dimensions(self):
        self.cfg['dimensions']['minecraft:the_nether']['enabled'] = True
        for dim in ['minecraft:the_end','minecraft:the_nether']:
            data={'dimension':dim,'replace_source_water':True,'operations':[{'from':[0,100,0],'block':'minecraft:stone'}]}
            with self.assertRaisesRegex(ValueError,'Source-water'):builder.compile_plan(data,self.cfg)
            data.pop('replace_source_water')
            data['operations'][0]['state']={'waterlogged':'true'}
            with self.assertRaisesRegex(ValueError,'Waterlogged'):builder.compile_plan(data,self.cfg)

    def test_same_geometry_in_different_dimensions_has_different_job_id(self):
        self.cfg['dimensions']['minecraft:the_nether']['enabled'] = True
        with tempfile.TemporaryDirectory() as d,patch.object(builder,'BASE',Path(d)),patch.object(builder,'config',return_value=self.cfg):
            p=Path(d)/'plans/example.json'
            ids=[]
            for dim in dimensions.BUILTIN_DIMENSIONS:
                builder.atomic(p,{'dimension':dim,'operations':[{'from':[0,100,0],'block':'minecraft:stone'}]})
                ids.append(builder.preview_build(str(p))['job_id'])
            self.assertEqual(len(set(ids)),3)

    def test_version_does_not_drift_between_mcp_and_worker(self):
        response,_=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'initialize'},False)
        self.assertEqual(response['result']['serverInfo']['version'],builder.VERSION)
        worker=(Path(__file__).parent/'mc_builder.sc').read_text()
        self.assertEqual(re.search(r"'worker_version' -> '([^']+)'",worker)[1],builder.VERSION)


class DimensionJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.cfg=example_config()
        self.cfg.update(shared_root=str(self.base/'shared'),protected_columns={'4,4':[0,255]})
        builder.atomic(self.base/'config.json',self.cfg)
        builder.atomic(self.base/'shared/policy.json',self.cfg)
        self.patches=[patch.object(builder,'BASE',self.base),patch.object(set_area,'BASE',self.base),
                      patch.object(builder,'health',return_value={'epoch':'test','mean_mspt':0,'pending':None}),
                      patch.object(set_area,'health',return_value={'epoch':'test','mean_mspt':0,'pending':None})]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def select(self,dim):
        with patch('sys.argv',['set_area.py','--dimension',dim,'--min','4','20','4','--max','5','20','4']),contextlib.redirect_stdout(io.StringIO()):
            set_area.main()

    def test_area_entry_point_syncs_dimension_to_both_policies(self):
        self.select('minecraft:the_end')
        local=builder.config();remote=json.loads((self.base/'shared/policy.json').read_text())
        self.assertEqual(local,remote)
        self.assertEqual(local['approved_area']['dimension'],'minecraft:the_end')
        self.assertEqual(local['protected_columns'],self.cfg['protected_columns'])
        with patch('sys.argv',['set_area.py','--disable']),contextlib.redirect_stdout(io.StringIO()):set_area.main()
        self.assertFalse(builder.config()['allow_write'])

    def test_pending_batch_blocks_area_changes_even_when_paused(self):
        builder.atomic(self.base/'jobs'/('a'*24)/'status.json',{'job_id':'a'*24,'state':'paused','pending_request':'b'*32})
        with self.assertRaisesRegex(ValueError,'unreconciled'):self.select('minecraft:the_end')
        self.assertFalse(builder.config()['allow_write'])

    def test_configure_entry_point_uses_live_heights_and_never_opens_writes(self):
        status={'worker_version':builder.VERSION,'allow_write':False,'approved_area':None,'pending':None,
                'dimension_info':[{'dimension':'minecraft:the_end','min_y':0,'max_y':1023}]}
        with patch.object(builder,'health',return_value=status),patch('sys.argv',['configure_dimensions.py','--enable','minecraft:the_end']),contextlib.redirect_stdout(io.StringIO()):
            configure_dimensions.main()
        cfg=builder.config()
        self.assertEqual(cfg['dimensions']['minecraft:the_end']['max_y'],1023)
        self.assertEqual(cfg,json.loads((self.base/'shared/policy.json').read_text()))
        self.assertFalse(cfg['allow_write'])

    def test_disable_dimension_preserves_heights_protection_and_other_dimensions(self):
        cfg=builder.config()
        cfg['dimensions']['minecraft:the_nether']['enabled']=True
        cfg['protected_columns_by_dimension']['minecraft:the_nether']={'1,1':[4,9]}
        builder.atomic(self.base/'config.json',cfg);builder.atomic(self.base/'shared/policy.json',cfg)
        status={'worker_version':builder.VERSION,'allow_write':False,'approved_area':None,'pending':None,'dimension_info':[]}
        with patch.object(builder,'health',return_value=status),patch('sys.argv',['configure_dimensions.py','--disable','minecraft:the_nether']),contextlib.redirect_stdout(io.StringIO()):
            configure_dimensions.main()
        result=builder.config()
        self.assertEqual(result['dimensions']['minecraft:the_nether'],{**cfg['dimensions']['minecraft:the_nether'],'enabled':False})
        self.assertEqual(result['dimensions']['minecraft:overworld'],cfg['dimensions']['minecraft:overworld'])
        self.assertEqual(result['protected_columns_by_dimension']['minecraft:the_nether'],{'1,1':[4,9]})
        self.assertFalse(result['allow_write'])

    def test_end_build_and_rollback_leave_overworld_at_same_coordinates_untouched(self):
        self.select('minecraft:the_end')
        source=self.base/'plans/end.json'
        builder.atomic(source,{'dimension':'minecraft:the_end','operations':[{'from':[4,20,4],'to':[5,20,4],'block':'minecraft:stone'}]})
        jid=builder.preview_build(str(source))['job_id']
        worlds={dimensions.OVERWORLD:{(4,20,4):'gold_block'},'minecraft:the_end':{}}
        sent=[]
        def read(dim,positions):
            return [{'pos':p,'loaded':True,'name':worlds[dim].get(tuple(p),'air'),'state':{},'has_block_entity':False} for p in positions]
        def request(action,request_id=None,**params):
            self.assertEqual(action,'batch')
            sent.append(params['dimension'])
            for op in params['operations']:
                worlds[params['dimension']][tuple(op['pos'])]=op['target'].removeprefix('minecraft:')
            builder.atomic(self.base/'shared/results'/(request_id+'.json'),{'ok':True,'applied':len(params['operations'])})
            return {'ok':True}
        with patch.object(builder,'read_positions',side_effect=read),patch.object(builder,'request',side_effect=request),patch('builder.subprocess.Popen'):
            builder.prepare_build(jid)
            self.select(dimensions.OVERWORLD)
            with self.assertRaisesRegex(ValueError,'outside'):builder.start_build(jid)
            self.select('minecraft:the_end')
            builder.start_build(jid);builder.run_job(jid)
            self.assertEqual(builder.build_status(jid)['state'],'completed')
            builder.rollback_build(jid);builder.rollback_job(jid)
        self.assertEqual(builder.build_status(jid)['state'],'rolled_back')
        self.assertTrue(sent and all(d=='minecraft:the_end' for d in sent))
        self.assertEqual(worlds[dimensions.OVERWORLD],{(4,20,4):'gold_block'})
        self.assertTrue(all(v=='air' for v in worlds['minecraft:the_end'].values()))


if __name__=='__main__':unittest.main()
