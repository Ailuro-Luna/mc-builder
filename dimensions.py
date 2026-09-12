"""Dimension policy shared by offline validation and administrative commands."""
import copy
import re

OVERWORLD = 'minecraft:overworld'
BUILTIN_DIMENSIONS = (OVERWORLD, 'minecraft:the_nether', 'minecraft:the_end')
DIMENSION_PATTERN = r'^[a-z0-9_.-]+:[a-z0-9_./-]+$'
LEGACY_DIMENSIONS = {OVERWORLD: {'enabled': True, 'min_y': -64, 'max_y': 319}}
HORIZONTAL_LIMIT = 29999983


def specification(dim, cfg):
    if not isinstance(dim, str) or not re.fullmatch(DIMENSION_PATTERN, dim):
        raise ValueError('Use a namespaced dimension ID, such as minecraft:the_end')
    entries = cfg.get('dimensions', LEGACY_DIMENSIONS)
    spec = entries.get(dim) if isinstance(entries, dict) else None
    if not isinstance(spec, dict) or spec.get('enabled') is not True:
        raise ValueError('Dimension is not enabled: ' + dim)
    lo, hi = spec.get('min_y'), spec.get('max_y')
    if type(lo) is not int or type(hi) is not int or lo > hi:
        raise ValueError('Invalid dimension height policy: ' + dim)
    return spec


def position(pos, dim, cfg):
    spec = specification(dim, cfg)
    if not isinstance(pos, list) or len(pos) != 3 or any(type(v) is not int for v in pos):
        raise ValueError('Coordinates must be three integers')
    if (abs(pos[0]) > HORIZONTAL_LIMIT or abs(pos[2]) > HORIZONTAL_LIMIT
            or not spec['min_y'] <= pos[1] <= spec['max_y']):
        raise ValueError('Coordinates outside configured limits for ' + dim)
    return pos


def protected(pos, dim, cfg):
    # Legacy columns always belong to the overworld, including after migration.
    maps = [cfg.get('protected_columns_by_dimension', {}).get(dim, {})]
    if dim == OVERWORLD:
        maps.append(cfg.get('protected_columns', {}))
    key = str(pos[0]) + ',' + str(pos[2])
    return any(key in columns and columns[key][0] <= pos[1] <= columns[key][1]
               for columns in maps)


def snapshot_bounds(plan, cfg):
    dim = plan['dimension']
    spec = specification(dim, cfg)
    position(plan['min'], dim, cfg)
    position(plan['max'], dim, cfg)
    margin = cfg['snapshot_margin']
    lower = [-HORIZONTAL_LIMIT, spec['min_y'], -HORIZONTAL_LIMIT]
    upper = [HORIZONTAL_LIMIT, spec['max_y'], HORIZONTAL_LIMIT]
    return ([max(lower[i], v - margin) for i, v in enumerate(plan['min'])],
            [min(upper[i], v + margin) for i, v in enumerate(plan['max'])])


def configure(cfg, live_dimensions, enabled):
    """Return a closed policy using live heights, preserving all old protections."""
    registry = {row['dimension']: row for row in live_dimensions}
    result = copy.deepcopy(cfg)
    entries = result.setdefault('dimensions', copy.deepcopy(LEGACY_DIMENSIONS))
    for dim in enabled:
        if dim not in registry:
            raise ValueError('Dimension does not exist on this server: ' + dim)
        row = registry[dim]
        entries[dim] = {'enabled': True, 'min_y': row['min_y'], 'max_y': row['max_y']}
        specification(dim, result)
    columns = result.setdefault('protected_columns_by_dimension', {}).setdefault(OVERWORLD, {})
    for key, span in result.get('protected_columns', {}).items():
        old = columns.get(key, span)
        columns[key] = [min(old[0], span[0]), max(old[1], span[1])]
    result.update(allow_write=False, approved_area=None, allow_source_water=False, allow_replace_blocks=[])
    return result
