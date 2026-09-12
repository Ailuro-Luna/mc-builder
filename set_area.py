"""Administrative area configuration; run only for a user-selected site."""
import argparse
import json
from pathlib import Path
from builder import BASE, config, atomic, position, global_lock, refuse_active_jobs, health
from dimensions import OVERWORLD


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min',nargs=3,type=int,dest='minimum')
    parser.add_argument('--max',nargs=3,type=int,dest='maximum')
    parser.add_argument('--disable',action='store_true')
    parser.add_argument('--dimension', default=OVERWORLD, help='Configured namespaced dimension ID')
    parser.add_argument('--source-water',action='store_true',help='Explicitly allow source-water foundations in this selected area')
    parser.add_argument('--replace-block',action='append',default=[],help='Explicit existing block name allowed as a replacement source')
    args=parser.parse_args()
    if args.disable:
        cfg=config()
        cfg.update(allow_write=False,approved_area=None,allow_source_water=False,allow_replace_blocks=[])
        atomic(Path(cfg['shared_root'])/'policy.json',cfg)
        atomic(BASE/'config.json',cfg)
        print(json.dumps({'allow_write':False,'approved_area':None,'allow_source_water':False,'allow_replace_blocks':[]}))
        return
    with global_lock():
        refuse_active_jobs()
        if health().get('pending') is not None:
            raise ValueError('Worker has a pending batch; reconcile before changing area')
        cfg=config()
        if args.disable:
            cfg.update(allow_write=False,approved_area=None,allow_source_water=False,allow_replace_blocks=[])
        else:
            lo=position(args.minimum,args.dimension,cfg);hi=position(args.maximum,args.dimension,cfg)
            if args.source_water and args.dimension != OVERWORLD:
                raise ValueError('Source-water replacement is only validated in the overworld')
            if any(b<a or b-a+1>cfg['max_extent'] for a,b in zip(lo,hi)):
                raise ValueError('Select an ordered area at most 32 blocks on each axis')
            replace=[]
            for material in args.replace_block:
                full=material if ':' in material else 'minecraft:'+material
                if full not in cfg['materials']:raise ValueError('Replacement source is not an enabled material: '+material)
                replace.append(full.removeprefix('minecraft:'))
            if len(set(replace))!=len(replace) or len(replace)>20:raise ValueError('Use at most 20 unique replacement sources')
            cfg.update(allow_write=True,approved_area={'dimension':args.dimension,'min':lo,'max':hi},
                       allow_source_water=args.source_water,allow_replace_blocks=replace)
        shared=Path(cfg['shared_root'])
        # Disable the server-side gate before changing either side's coordinates.
        atomic(shared/'policy.json',{**cfg,'allow_write':False})
        atomic(BASE/'config.json',cfg)
        atomic(shared/'policy.json',cfg)
    print(json.dumps({'allow_write':cfg['allow_write'],'approved_area':cfg['approved_area'],
                      'allow_source_water':cfg['allow_source_water'],'allow_replace_blocks':cfg['allow_replace_blocks']},ensure_ascii=False))


if __name__=='__main__':main()
