"""Enable explicitly selected dimensions using their live server heights; keep writes off."""
import argparse
from pathlib import Path
import json
import builder
import dimensions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='append', default=[], help='Exact namespaced dimension ID; repeat as needed')
    parser.add_argument('--disable', action='append', default=[], help='Disable a configured dimension without deleting its heights or protections')
    args = parser.parse_args()
    if not (args.enable or args.disable) or set(args.enable) & set(args.disable):
        raise ValueError('Select dimensions to enable or disable, without overlap')
    with builder.global_lock():
        builder.refuse_active_jobs()
        cfg = builder.config()
        status = builder.health()
        if (cfg['allow_write'] or cfg['approved_area'] is not None
                or status['allow_write'] or status['approved_area'] is not None
                or status.get('pending') is not None):
            raise ValueError('Close both write gates and resolve pending work before configuring dimensions')
        if status.get('worker_version') != builder.VERSION:
            raise ValueError('Update the worker before configuring dimensions')
        updated = dimensions.configure(cfg, status['dimension_info'], args.enable)
        for dim in args.disable:
            if dim not in updated['dimensions']:
                raise ValueError('Dimension is not configured: ' + dim)
            updated['dimensions'][dim]['enabled'] = False
        policy = Path(cfg['shared_root'])/'policy.json'
        if json.loads(policy.read_text()) != cfg:
            raise ValueError('Local/server policies differ; reconcile before configuring dimensions')
        # All intermediate states keep writing disabled.
        builder.atomic(policy, updated)
        builder.atomic(builder.BASE/'config.json', updated)
    print(json.dumps({'dimensions': updated['dimensions'], 'allow_write': False,
                      'approved_area': None, 'protected_overworld_columns':
                      len(updated['protected_columns_by_dimension'][dimensions.OVERWORLD])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
