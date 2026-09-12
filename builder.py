"""Local blueprint storage, bounded queries, and supervised construction jobs."""
import argparse
import collections
import contextlib
import fcntl
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from rcon import Rcon
import dimensions

BASE = Path(__file__).resolve().parent
VERSION = '0.4.0'


def config():
    return json.loads((BASE/'config.json').read_text())


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(tmp, 'x', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def request(action, request_id=None, **params):
    cfg = config()
    rid = request_id or uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}', rid):
        raise ValueError('Invalid request ID')
    root = Path(cfg['shared_root'])
    worker = cfg.get('worker_name', root.name)
    if not isinstance(worker, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', worker):
        raise ValueError('Invalid configured worker name')
    req = {'action': action, **params}
    atomic(root/'requests'/(rid+'.json'), req)
    with Rcon(cfg['server_root'], timeout=30) as r:
        response = r.command(worker+' call '+rid)
    result = decode_reply(response, 'JSON')
    if not result.get('ok'):
        raise RuntimeError(str(result.get('error', 'Worker rejected request')))
    return result


def decode_reply(response, kind):
    # Accept existing deployment prefixes without publishing any site identifier.
    marker = re.search(r'\b[A-Z][A-Z0-9_]*_' + re.escape(kind) + ':', response)
    if marker is None:
        raise RuntimeError('Worker returned no structured response')
    result, _ = json.JSONDecoder().raw_decode(response[marker.end():])
    return result


def health():
    return request('health')


def search_blocks(query):
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError('Use a material name of 1–80 characters')
    return request('materials', pattern=re.escape(query))


def describe_blocks(blocks):
    """Read registry defaults and fully specified states without placing blocks.

    This console query works with the existing worker, so adding a read-only
    capability does not invalidate prepared jobs by reloading Scarpet.
    """
    pattern = r'minecraft:[a-z0-9_]+(?:\[[a-z0-9_=,]+\])?'
    if (not isinstance(blocks, list) or not 1 <= len(blocks) <= 16
            or any(not isinstance(b, str) or not re.fullmatch(pattern, b) for b in blocks)):
        raise ValueError('Use 1–16 exact vanilla block names, optionally with states')
    literals = '[' + ','.join("'" + b + "'" for b in blocks) + ']'
    expression = ("print('MC_BUILDER_MATERIALS:'+encode_json(map(" + literals +
                  ", {'requested' -> _, 'name' -> str(block(_)), "
                  "'state' -> block_state(block(_)), 'has_block_entity' -> block_data(block(_)) != null})))")
    with Rcon(config()['server_root'], timeout=30) as r:
        response = r.command('script run ' + expression)
    rows = decode_reply(response, 'MATERIALS')
    if not isinstance(rows, list) or len(rows) != len(blocks):
        raise RuntimeError('Incomplete registry description')
    return rows


def read_entities(minimum, maximum, dim='minecraft:overworld'):
    """Read loaded entities only. Width/height are NOT modded render bounds."""
    cfg=config()
    position(minimum,dim,cfg);position(maximum,dim,cfg)
    sizes=[b-a+1 for a,b in zip(minimum,maximum)]
    if min(sizes)<1 or max(sizes[0],sizes[2])>256 or sizes[1]>2048:
        raise ValueError('Entity query is limited to 256x2048x256')
    center=[(a+b+1)/2 for a,b in zip(minimum,maximum)]
    half=[v/2 for v in sizes]
    expression=("in_dimension('"+dim+"', print('MC_BUILDER_ENTITIES:'+encode_json(map(entity_area('*',"+
        json.dumps(center)+','+json.dumps(half)+"), {'type' -> query(_,'type'), 'uuid' -> query(_,'uuid'), "
        "'pos' -> query(_,'pos'), 'width' -> query(_,'width'), 'height' -> query(_,'height')}))))")
    with Rcon(cfg['server_root'],timeout=30) as r:
        response=r.command('script run '+expression)
    rows=decode_reply(response, 'ENTITIES')
    if not isinstance(rows,list) or any(
            not isinstance(row,dict) or not isinstance(row.get('type'),str)
            or not isinstance(row.get('uuid'),str)
            or not isinstance(row.get('pos'),list) or len(row['pos'])!=3
            or any(type(v) not in (int,float) or not math.isfinite(v)
                   for v in row['pos']+[row.get('width'),row.get('height')])
            for row in rows):
        raise RuntimeError('Incomplete entity query; do not infer empty space')
    return {'dimension':dim,'min':minimum,'max':maximum,'entities':rows,
            'note':'Loaded entities only; modded contraptions require separate geometry inspection'}


def position(pos, dim=dimensions.OVERWORLD, cfg=None):
    return dimensions.position(pos, dim, config() if cfg is None else cfg)


def dimension(dim, cfg=None):
    dimensions.specification(dim, config() if cfg is None else cfg)
    return dim


def replaceable(old, plan):
    if not old.get('loaded') or old.get('has_block_entity'):
        return False
    if old.get('name') in ('air', 'cave_air', 'void_air'):
        return old.get('state') == {}
    if old.get('name') in plan.get('replace_blocks', []):
        return old.get('state') == {}
    return (plan.get('replace_source_water') is True
            and old.get('name') == 'water' and old.get('state') == {'level':'0'})


def protected(pos, cfg, dim=dimensions.OVERWORLD):
    return dimensions.protected(pos, dim, cfg)


def read_positions(dim, positions):
    cfg = config()
    dimension(dim, cfg)
    for p in positions:
        position(p, dim, cfg)
    if len(positions) > 64:
        raise ValueError('At most 64 positions per read request')
    return request('read', dimension=dim, positions=positions)['blocks']


def inspect_area(minimum, maximum, dim='minecraft:overworld'):
    cfg = config()
    position(minimum, dim, cfg); position(maximum, dim, cfg)
    sizes = [b-a+1 for a, b in zip(minimum, maximum)]
    if min(sizes) < 1 or max(sizes) > 32 or sizes[0]*sizes[1]*sizes[2] > 4096:
        raise ValueError('Inspection is limited to 4096 positions, maximum 32 on each axis')
    positions = [list(p) for p in itertools.product(*(range(a, b+1) for a,b in zip(minimum,maximum)))]
    rows = []
    for i in range(0, len(positions), 64):
        rows += read_positions(dim, positions[i:i+64])
    sid = uuid.uuid4().hex
    path = BASE/'scans'/(sid+'.json')
    atomic(path, rows)
    counts = collections.Counter(b.get('name', 'unloaded') for b in rows)
    return {'dimension':dim, 'min':minimum, 'max':maximum, 'positions':len(rows),
            'unloaded':counts.get('unloaded',0), 'materials':counts.most_common(20),
            'block_entities':sum(bool(b.get('has_block_entity')) for b in rows), 'full_data':str(path)}


def compile_plan(data, cfg=None):
    cfg = cfg or config()
    dim = dimension(data['dimension'], cfg)
    if type(data.get('replace_source_water', False)) is not bool:
        raise ValueError('replace_source_water must be a boolean')
    if data.get('replace_source_water') and dim != dimensions.OVERWORLD:
        raise ValueError('Source-water replacement is only validated in the overworld')
    replace_blocks=data.get('replace_blocks', [])
    if not isinstance(replace_blocks,list) or len(replace_blocks)>20 or len(set(replace_blocks))!=len(replace_blocks):
        raise ValueError('replace_blocks must be a unique list of at most 20 enabled blocks')
    canonical_replace=[]
    for material in replace_blocks:
        if not isinstance(material,str) or material not in cfg['materials']:
            raise ValueError('Replacement source is not an enabled material: '+str(material))
        canonical_replace.append(material.removeprefix('minecraft:'))
    blocks = {}
    expanded = 0
    operations = data.get('operations', [])
    if not isinstance(operations, list) or not 1 <= len(operations) <= 10000:
        raise ValueError('Blueprint needs 1–10000 operations')
    for op in operations:
        material = op['block']
        if material not in cfg['materials']:
            raise ValueError('Material is not enabled for initial construction: '+str(material))
        state=op.get('state',{})
        if isinstance(state, dict) and dim != dimensions.OVERWORLD and state.get('waterlogged') == 'true':
            raise ValueError('Waterlogged targets are only validated in the overworld')
        if (not isinstance(state,dict) or len(state)>16 or
            any(not isinstance(k,str) or not re.fullmatch('[a-z0-9_]{1,40}',k) or
                not isinstance(v,str) or not re.fullmatch('[a-z0-9_\-]{1,40}',v) for k,v in state.items())):
            raise ValueError('Block state must contain at most 16 simple string properties')
        lo = position(op['from'], dim, cfg); hi = position(op.get('to', lo), dim, cfg)
        sizes = [b-a+1 for a,b in zip(lo,hi)]
        if min(sizes) < 1 or max(sizes) > cfg['max_extent'] or sizes[0]*sizes[1]*sizes[2] > cfg['max_blocks']:
            raise ValueError('Operation is reversed or too large')
        for p in itertools.product(*(range(a,b+1) for a,b in zip(lo,hi))):
            expanded += 1
            if expanded > 100000:
                raise ValueError('Blueprint repeats too many operations')
            if op.get('hollow', False) and all(a < v < b for a,v,b in zip(lo,p,hi)):
                continue
            blocks[p] = (material,state)
            if len(blocks) > cfg['max_blocks']:
                raise ValueError('Blueprint exceeds total block limit')
    if not blocks:
        raise ValueError('Empty blueprint')
    lo = [min(p[i] for p in blocks) for i in range(3)]
    hi = [max(p[i] for p in blocks) for i in range(3)]
    if any(b-a+1 > cfg['max_extent'] for a,b in zip(lo,hi)):
        raise ValueError('Combined blueprint exceeds extent limit')
    compiled_blocks=[]
    for p in sorted(blocks,key=lambda p:(p[1],p[0],p[2])):
        material,state=blocks[p]
        row={'pos':list(p),'target':material}
        if state:row['target_state']=state
        compiled_blocks.append(row)
    compiled = {'dimension':data['dimension'], 'min':lo, 'max':hi, 'blocks':compiled_blocks}
    if data.get('replace_source_water'):
        compiled['replace_source_water'] = True
    if canonical_replace:
        compiled['replace_blocks'] = canonical_replace
    return compiled


def preview_build(blueprint_path):
    path = Path(blueprint_path).resolve()
    if not path.is_relative_to(BASE/'plans') or path.suffix != '.json' or path.stat().st_size > 2*1024*1024:
        raise ValueError('Use a JSON blueprint inside mc-builder/plans, at most 2 MiB')
    compiled = compile_plan(json.loads(path.read_text()))
    digest = hashlib.sha256(json.dumps(compiled, sort_keys=True).encode()).hexdigest()
    jid = digest[:24]
    dest = BASE/'jobs'/jid
    if not (dest/'plan.json').exists():
        atomic(dest/'plan.json', compiled)
        atomic(dest/'status.json', {'job_id':jid, 'state':'previewed', 'completed':0, 'total':len(compiled['blocks']), 'digest':digest})
    return {'job_id':jid, 'dimension':compiled['dimension'], 'min':compiled['min'], 'max':compiled['max'],
            'blocks':len(compiled['blocks']), 'materials':dict(collections.Counter(b['target'] for b in compiled['blocks'])),
            'write_enabled':config()['allow_write'], 'note':'Geometry preview; no world edits or site snapshot yet'}


def job_path(jid):
    if not re.fullmatch('[a-f0-9]{24}', jid):
        raise ValueError('Invalid job ID')
    path = BASE/'jobs'/jid
    if not (path/'status.json').exists():
        raise ValueError('Unknown job')
    return path


def load_plan(root):
    plan=json.loads((root/'plan.json').read_text())
    digest=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    if json.loads((root/'status.json').read_text()).get('digest') != digest:
        raise ValueError('Blueprint changed after preview; create a new preview instead')
    return plan


def read_job_state(job_id):
    root = job_path(job_id)
    state = json.loads((root/'status.json').read_text())
    state['pause_requested'] = (root/'pause').exists()
    return state


def build_status(job_id):
    state=read_job_state(job_id)
    # Old status files remain untouched; derive their dimension from the plan.
    state['dimension'] = load_plan(job_path(job_id))['dimension']
    if 'rollback_conflicts' in state:
        conflicts=state.pop('rollback_conflicts')
        state['rollback_conflict_count']=len(conflicts)
        state['rollback_conflict_samples']=conflicts[:5]
    state['full_status_file']=str(job_path(job_id)/'status.json')
    return state


def load_snapshot(root, plan, state):
    """Bind new snapshots to their dimension and exact blueprint; retain legacy jobs."""
    rows = json.loads((root/'snapshot.json').read_text())
    if state.get('snapshot_schema') == 1:
        metadata = json.loads((root/'snapshot-meta.json').read_text())
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if (metadata.get('dimension') != plan['dimension']
                or metadata.get('plan_digest') != state['digest']
                or metadata.get('rows_digest') != digest):
            raise ValueError('Snapshot dimension, blueprint, or content mismatch')
    return {tuple(row['pos']): row for row in rows}


def approved(plan):
    cfg = config(); area = cfg['approved_area']
    dim = dimension(plan['dimension'], cfg)
    position(plan['min'], dim, cfg); position(plan['max'], dim, cfg)
    for op in plan['blocks']:
        position(op['pos'], dim, cfg)
    if not cfg['allow_write'] or not area:
        raise ValueError('Construction is not enabled: choose a specific area first')
    if plan['dimension'] != area['dimension'] or any(a < lo or b > hi for a,b,lo,hi in zip(plan['min'],plan['max'],area['min'],area['max'])):
        raise ValueError('Blueprint is outside the approved area')
    if plan.get('replace_source_water') and not cfg.get('allow_source_water', False):
        raise ValueError('Source-water foundations are not enabled for this site')
    if plan.get('replace_source_water') and dim != dimensions.OVERWORLD:
        raise ValueError('Source-water replacement is only validated in the overworld')
    if not set(plan.get('replace_blocks',[])).issubset(set(cfg.get('allow_replace_blocks',[]))):
        raise ValueError('Existing-block replacement is not enabled for this site')
    if any(protected(op['pos'], cfg, dim) for op in plan['blocks']):
        raise ValueError('Blueprint intersects a protected path or existing feature')
    return cfg


@contextlib.contextmanager
def global_lock(block=False):
    with open(BASE/'runner.lock', 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ValueError('Another construction operation is active')
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def prepare_build(job_id):
    root = job_path(job_id)
    plan = load_plan(root); cfg = approved(plan)
    with global_lock():
        state = read_job_state(job_id)
        if state['state'] not in ('previewed','prepared'):
            raise ValueError('Cannot replace snapshots after construction has started')
        lo, hi = dimensions.snapshot_bounds(plan, cfg)
        positions = [list(p) for p in itertools.product(*(range(a,b+1) for a,b in zip(lo,hi)))]
        if len(positions) > cfg['max_snapshot_blocks']:
            raise ValueError('Snapshot too large')
        rows = []
        for i in range(0,len(positions),64):
            batch = read_positions(plan['dimension'],positions[i:i+64])
            if any(not b['loaded'] for b in batch):
                raise ValueError('Keep the entire snapshot area loaded before preparation')
            rows.extend(batch)
        if any(b['has_block_entity'] for b in rows):
            raise ValueError('Snapshot neighborhood contains block entities; choose a clear initial site')
        by_pos = {tuple(b['pos']):b for b in rows}
        for op in plan['blocks']:
            old = by_pos[tuple(op['pos'])]
            if not replaceable(old, plan):
                raise ValueError('Site contains protected solid blocks, non-source water, or unsupported state')
        atomic(root/'snapshot.json', rows)
        if json.loads((root/'snapshot.json').read_text()) != rows:
            raise RuntimeError('Snapshot readback failed')
        atomic(root/'snapshot-meta.json', {
            'schema': 1, 'dimension': plan['dimension'], 'plan_digest': state['digest'],
            'rows_digest': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            'min': lo, 'max': hi})
        state.update(state='prepared', snapshot_positions=len(rows), prepared_epoch=health()['epoch'],
                     dimension=plan['dimension'], snapshot_schema=1,
                     source_water_positions=sum(by_pos[tuple(op['pos'])]['name'] == 'water' for op in plan['blocks']),
                     replaced_existing_positions=sum(by_pos[tuple(op['pos'])]['name'] in plan.get('replace_blocks',[]) for op in plan['blocks']))
        state.pop('pause_requested',None)
        atomic(root/'status.json',state)
    return state


def start_build(job_id):
    root = job_path(job_id)
    approved(load_plan(root))
    with global_lock():
        refuse_active_jobs()
        state = read_job_state(job_id)
        if state['state'] not in ('prepared','paused') or state.get('error'):
            raise ValueError('Prepare first; errors or uncertain batches require reconciliation')
        if 'rollback_cursor' in state:
            raise ValueError('Use rollback_build to continue a paused rollback')
        if state.get('prepared_epoch') != health()['epoch']:
            raise ValueError('Server/worker restarted; recheck the site before continuing')
        load_snapshot(root, load_plan(root), state)
        (root/'pause').unlink(missing_ok=True)
        (root/'cancel').unlink(missing_ok=True)
        atomic(root/'status.json',{**state,'state':'starting'})
        with (root/'runner.log').open('a') as log:
            subprocess.Popen([sys.executable,str(BASE/'builder.py'),'run',job_id], stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log,start_new_session=True)
    return {'job_id':job_id,'state':'starting'}


def pause_build(job_id):
    root = job_path(job_id)
    (root/'pause').touch(mode=0o600)
    return {'job_id':job_id,'pause_requested':True,'note':'Stops at the next batch boundary (at most 20 positions)'}


def cancel_build(job_id):
    root = job_path(job_id)
    (root/'cancel').touch(mode=0o600)
    return pause_build(job_id)


def rollback_build(job_id):
    root = job_path(job_id)
    approved(load_plan(root))
    with global_lock():
        refuse_active_jobs()
        state = read_job_state(job_id)
        if state['state'] not in ('completed','paused','cancelled') or state.get('pending_request'):
            raise ValueError('Stop construction and resolve any uncertain batch before rollback')
        load_snapshot(root, load_plan(root), state)
        if state.get('rollback_cursor') is None:
            state['rollback_cursor'] = state['completed']-1
            state['rollback_conflicts'] = []
        state.update(state='rollback_starting',rollback_epoch=health()['epoch'])
        state.pop('error',None)
        (root/'pause').unlink(missing_ok=True)
        (root/'cancel').unlink(missing_ok=True)
        atomic(root/'status.json',state)
        with (root/'runner.log').open('a') as log:
            subprocess.Popen([sys.executable,str(BASE/'builder.py'),'rollback-run',job_id],
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return {'job_id':job_id,'state':'rollback_starting'}


def refuse_active_jobs():
    for path in (BASE/'jobs').glob('*/status.json'):
        state = json.loads(path.read_text())
        if state['state'] in ('starting','running','rollback_starting','rolling_back') or state.get('pending_request'):
            raise ValueError('An active or unreconciled job already exists: '+state['job_id'])


def wait_batch(rid, cfg):
    result_path = Path(cfg['shared_root'])/'results'/(rid+'.json')
    deadline = time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            return json.loads(result_path.read_text())
        except (FileNotFoundError,json.JSONDecodeError):
            time.sleep(0.05)
    raise RuntimeError('Batch outcome uncertain; inspect journal before resuming')


def rollback_job(jid):
    root = job_path(jid)
    with global_lock(block=True):
        state = read_job_state(jid)
        plan = load_plan(root)
        try:
            before = load_snapshot(root, plan, state)
            while state['rollback_cursor'] >= 0:
                cfg = approved(plan)
                if (root/'pause').exists():
                    state['state']='paused'; break
                status = health()
                if status['epoch'] != state['rollback_epoch']:
                    raise RuntimeError('Worker restarted during rollback; reconcile before continuing')
                op = plan['blocks'][state['rollback_cursor']]
                expected = {'loaded':True,'pos':op['pos'],'name':op['target'].split(':')[1],
                            'state':op.get('target_state',{}),'has_block_entity':False}
                current = read_positions(plan['dimension'],[op['pos']])[0]
                old = before[tuple(op['pos'])]
                if current == old:
                    state['rollback_cursor']-=1
                elif current != expected:
                    state['rollback_conflicts'].append(op['pos'])
                    state['rollback_cursor']-=1
                else:
                    rid = uuid.uuid4().hex
                    state.update(state='rolling_back',pending_request=rid)
                    atomic(root/'status.json',state)
                    request('batch',request_id=rid,dimension=plan['dimension'],mode='rollback',dry_run=False,
                            replace_source_water=plan.get('replace_source_water', False),
                            replace_blocks=plan.get('replace_blocks',[]),
                            operations=[{'pos':op['pos'],'target':old['name'],'restore_state':old['state'],'expected':expected}])
                    result = wait_batch(rid,cfg)
                    if result.get('applied') == 1:
                        state['rollback_cursor']-=1
                    if result.get('uncertain'):
                        raise RuntimeError('Uncertain rollback batch; inspect journal before continuing')
                    state.pop('pending_request',None)
                    if not result['ok']:
                        raise RuntimeError('Rollback stopped: '+str(result.get('error')))
                atomic(root/'status.json',state)
            else:
                state['state']='rolled_back_with_conflicts' if state['rollback_conflicts'] else 'rolled_back'
        except Exception as e:
            state.update(state='paused',error=str(e))
        atomic(root/'status.json',state)


def run_job(jid):
    root = job_path(jid)
    with global_lock(block=True):
        state = read_job_state(jid)
        plan = load_plan(root)
        try:
            before = load_snapshot(root, plan, state)
            while state['completed'] < state['total']:
                cfg = approved(plan)
                if (root/'pause').exists():
                    state['state']='cancelled' if (root/'cancel').exists() else 'paused'; break
                status = health()
                if status['epoch'] != state['prepared_epoch']:
                    raise RuntimeError('Worker restarted; manual reconciliation required')
                if status['mean_mspt'] > cfg['pause_mspt']:
                    state['state']='paused'; state['pause_reason']='server_load_high'; break
                ops = [{**op,'expected':before[tuple(op['pos'])]} for op in plan['blocks'][state['completed']:state['completed']+cfg['batch_size']]]
                rid = uuid.uuid4().hex
                state.update(state='running', pending_request=rid)
                atomic(root/'status.json',state)
                request('batch',request_id=rid,dimension=plan['dimension'],mode='build',operations=ops,dry_run=False,
                        replace_source_water=plan.get('replace_source_water', False),
                        replace_blocks=plan.get('replace_blocks',[]))
                result = wait_batch(rid,cfg)
                state['completed'] += result.get('applied',0)
                if result.get('uncertain'):
                    raise RuntimeError('Uncertain construction batch; inspect journal before continuing')
                state.pop('pending_request',None)
                if not result['ok']:
                    raise RuntimeError('Batch stopped: '+str(result.get('error')))
                if not result.get('applied'):
                    raise RuntimeError('Batch made no progress')
                atomic(root/'status.json',state)
            else:
                state['state']='completed'
        except Exception as e:
            state.update(state='paused',error=str(e))
        atomic(root/'status.json',state)


def scheduler_test(dim='minecraft:overworld'):
    dimension(dim)
    refuse_active_jobs()
    rid = uuid.uuid4().hex
    request('batch',request_id=rid,dimension=dim,operations=[],dry_run=True)
    return wait_batch(rid,config())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['health','scheduler-test','run','rollback-run','preview'])
    parser.add_argument('argument',nargs='?')
    args = parser.parse_args()
    if args.command=='run': run_job(args.argument)
    elif args.command=='rollback-run': rollback_job(args.argument)
    else:
        result = health() if args.command=='health' else scheduler_test() if args.command=='scheduler-test' else preview_build(args.argument)
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
