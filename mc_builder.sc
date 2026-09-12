// Fixed, console-only bridge. Loading or restarting never resumes placement.
__config() -> {
  'scope' -> 'global',
  'command_permission' -> 'server',
  'commands' -> {'call <request>' -> '_request'},
  'arguments' -> {'request' -> {'type' -> 'term'}}
};

global_pending = null;
global_epoch = str(unix_time());
global_registry = block_list();

_read(name) -> read_file('mc_builder/'+name, 'shared_json');
_write(name, value) -> (
  if(!write_file('mc_builder/'+name, 'shared_json', value), throw('file_write_failed'));
  value
);
_policy() -> (
  cfg = _read('policy');
  if(type(cfg) != 'map', throw('missing_policy'));
  cfg
);
_contains(items, value) -> first(items, _ == value) != null;
_mspt() -> (
  times = system_info('server_last_tick_times');
  reduce(times, _a+_, 0)/length(times)
);
_state(pos) -> (
  if(generation_status(pos) != 'full', return({'loaded' -> false, 'pos' -> pos}));
  b = block(pos);
  {'loaded' -> true, 'pos' -> pos, 'name' -> str(b), 'state' -> block_state(b),
   'has_block_entity' -> block_data(b) != null}
);
_equal(a, b) -> a:'loaded' && b:'loaded' && a:'name' == b:'name' && a:'state' == b:'state' && a:'has_block_entity' == b:'has_block_entity';
_bounds(pos, area) -> (
  if(type(pos) != 'list' || length(pos) != 3, return(false));
  all(range(3), pos:_ == floor(pos:_) && pos:_ >= area:'min':_ && pos:_ <= area:'max':_)
);
_canonical(dim) -> if((str(dim) ~ ':') != null, str(dim), 'minecraft:'+str(dim));
_dimension_info(dim) -> in_dimension(dim, {
  'dimension' -> _canonical(dim),
  'min_y' -> system_info('world_bottom'),
  // world_top is the exclusive upper bound in the server HeightLimitView.
  'max_y' -> system_info('world_top')-1
});
_dimension_policy(dim, cfg) -> (
  if(type(dim) != 'string' || !(dim ~ '^[a-z0-9_.-]+:[a-z0-9_./-]+$'), throw('invalid_dimension_id'));
  entries = cfg:'dimensions';
  if(entries == null, entries = {'minecraft:overworld' -> {'enabled' -> true, 'min_y' -> -64, 'max_y' -> 319}});
  if(type(entries) != 'map', throw('invalid_dimension_policy'));
  spec = entries:dim;
  if(type(spec) != 'map' || spec:'enabled' != true, throw('dimension_not_enabled'));
  if(type(spec:'min_y') != 'number' || type(spec:'max_y') != 'number', throw('invalid_dimension_height'));
  if(spec:'min_y' != floor(spec:'min_y') || spec:'max_y' != floor(spec:'max_y') || spec:'min_y' > spec:'max_y', throw('invalid_dimension_height'));
  if(!_contains(map(system_info('world_dimensions'), _canonical(_)), dim), throw('dimension_not_found'));
  live = _dimension_info(dim);
  if(spec:'min_y' < live:'min_y' || spec:'max_y' > live:'max_y', throw('dimension_height_mismatch'));
  spec
);
_valid_position(pos, spec) -> (
  if(type(pos) != 'list' || length(pos) != 3, return(false));
  all(pos, type(_) == 'number' && _ == floor(_)) &&
    abs(pos:0) <= 29999983 && abs(pos:2) <= 29999983 && pos:1 >= spec:'min_y' && pos:1 <= spec:'max_y'
);
_in_columns(pos, columns) -> (
  if(type(columns) != 'map', return(false));
  vertical = columns:(str(pos:0)+','+str(pos:2));
  type(vertical) == 'list' && pos:1 >= vertical:0 && pos:1 <= vertical:1
);
_protected(pos, cfg, dim) -> (
  legacy = dim == 'minecraft:overworld' && _in_columns(pos, cfg:'protected_columns');
  scoped = cfg:'protected_columns_by_dimension';
  legacy || (type(scoped) == 'map' && _in_columns(pos, scoped:dim))
);
_replaceable(current, req) -> (
  if(_contains(['air','cave_air','void_air'], current:'name'), return(current:'state' == {}));
  if(_contains(req:'replace_blocks',current:'name'), return(current:'state' == {}));
  req:'replace_source_water' == true && current:'name' == 'water' && current:'state' == {'level' -> '0'}
);
_permit_policy(req, cfg) -> (
  spec = _dimension_policy(req:'dimension', cfg);
  if(!cfg:'allow_write' || type(cfg:'approved_area') != 'map', throw('construction_area_not_enabled'));
  area = cfg:'approved_area';
  if(req:'dimension' != area:'dimension', throw('wrong_dimension'));
  if(!_valid_position(area:'min',spec) || !_valid_position(area:'max',spec) ||
    !all(range(3), area:'max':_ >= area:'min':_ && area:'max':_-area:'min':_+1 <= min(cfg:'max_extent',32)), throw('invalid_approved_area'));
  if(req:'replace_source_water' == true && req:'dimension' != 'minecraft:overworld', throw('source_water_dimension_not_validated'));
  if(req:'replace_source_water' == true && cfg:'allow_source_water' != true, throw('source_water_not_enabled'));
  for(req:'replace_blocks',if(!_contains(cfg:'allow_replace_blocks',_),throw('existing_replacement_not_enabled')));
  if(length(req:'operations') > cfg:'batch_size', throw('batch_too_large'));
  for(req:'operations',
    if(!_valid_position(_:'pos', spec), throw('position_outside_dimension'));
    if(!_bounds(_:'pos', area), throw('outside_approved_area'));
    if(_protected(_:'pos', cfg, req:'dimension'), throw('protected_existing_feature'));
    if(req:'dimension' != 'minecraft:overworld' && type(_:'target_state') == 'map' && _:'target_state':'waterlogged' == 'true', throw('waterlogged_dimension_not_validated'));
    if(req:'mode' != 'rollback' && !_contains(cfg:'materials', _:'target'), throw('material_not_enabled'));
    if(req:'mode' == 'rollback',
      valid_air = _contains(['air','cave_air','void_air'], _:'target') && (_:'restore_state' == null || _:'restore_state' == {});
      valid_water = cfg:'allow_source_water' == true && req:'replace_source_water' == true && _:'target' == 'water' && _:'restore_state' == {'level' -> '0'};
      valid_existing = _contains(cfg:'allow_replace_blocks', _:'target') && _:'restore_state' == {};
      if(!valid_air && !valid_water && !valid_existing, throw('unsupported_rollback_target'))
    )
  );
  if(_mspt() > cfg:'pause_mspt', throw('server_load_high'));
  cfg
);
_permit(req) -> _permit_policy(req, _policy());
_apply(id, req) -> (
  if(global_pending != id, return());
  result = try(
    if(req:'dry_run',
      _dimension_policy(req:'dimension', _policy());
      {'ok' -> true, 'dry_run' -> true, 'applied' -> 0, 'dimension' -> req:'dimension', 'epoch' -> global_epoch},
      cfg = _permit(req);
      in_dimension(req:'dimension',
        // Record the complete batch before changing the first position.
        _write('journal/'+id, {'state' -> 'attempting', 'request' -> req, 'epoch' -> global_epoch});
        applied = 0;
        failure = null;
        started = time();
        for(req:'operations',
          op = _;
          current = _state(op:'pos');
          if(!_equal(current, op:'expected'), failure = 'site_changed_or_unloaded'; break());
          if(current:'has_block_entity', failure = 'block_entity_not_supported'; break());
          if(req:'mode' != 'rollback' && !_replaceable(current, req), failure = 'replace_existing_disabled'; break());
          if(req:'mode' == 'rollback' && type(op:'restore_state') == 'map',
            set(op:'pos', block(op:'target'), op:'restore_state'),
            if(type(op:'target_state') == 'map',
              set(op:'pos', block(op:'target'), op:'target_state'),
              set(op:'pos', block(op:'target'))
            )
          );
          after = _state(op:'pos');
          target = block(op:'target');
          target_state = if(type(op:'target_state') == 'map',op:'target_state',block_state(target));
          if(after:'name' != str(target) || after:'state' != target_state, failure = 'placement_verification_failed'; break());
          applied += 1;
          if(time()-started >= 2, break())
        );
        {'ok' -> failure == null, 'applied' -> applied, 'error' -> failure,
         'uncertain' -> failure == 'placement_verification_failed', 'epoch' -> global_epoch}
      )
    ),
    {'ok' -> false, 'applied' -> 0, 'error' -> str(_), 'uncertain' -> true, 'epoch' -> global_epoch}
  );
  _write('results/'+id, result);
  global_pending = null
);
_dispatch(id, req) -> (
  action = req:'action';
  if(action == 'health',
    cfg = _policy();
    return({'ok' -> true, 'worker_version' -> '0.4.0', 'epoch' -> global_epoch,
      'minecraft' -> system_info('game_version'), 'carpet' -> system_info('server_mods'):'carpet',
      'spawn' -> system_info('world_spawn_point'), 'dimensions' -> system_info('world_dimensions'),
      'dimension_info' -> map(system_info('world_dimensions'), _dimension_info(_)),
      'configured_dimensions' -> if(cfg:'dimensions' == null, {'minecraft:overworld' -> {'enabled' -> true, 'min_y' -> -64, 'max_y' -> 319}}, cfg:'dimensions'),
      'mean_mspt' -> _mspt(), 'registered_blocks' -> length(global_registry),
      'allow_write' -> cfg:'allow_write', 'approved_area' -> cfg:'approved_area', 'pending' -> global_pending,
      'allow_source_water' -> cfg:'allow_source_water' == true,
      'allow_replace_blocks' -> if(type(cfg:'allow_replace_blocks') == 'list',cfg:'allow_replace_blocks',[]),
      'protected_columns' -> if(type(cfg:'protected_columns') == 'map',length(cfg:'protected_columns'),0)})
  );
  if(action == 'materials',
    hits = filter(global_registry, (str(_) ~ (req:'pattern')) != null);
    return({'ok' -> true, 'matches' -> length(hits), 'blocks' -> slice(hits, 0, min(length(hits), 30))})
  );
  if(action == 'read',
    spec = _dimension_policy(req:'dimension', _policy());
    if(length(req:'positions') > 64, throw('read_batch_too_large'));
    if(!all(req:'positions', _valid_position(_,spec)), throw('position_outside_dimension'));
    return(in_dimension(req:'dimension', {'ok' -> true, 'blocks' -> map(req:'positions', _state(_))}))
  );
  if(action == 'cancel_pending',
    if(global_pending != null,
      _write('results/'+global_pending, {'ok' -> false, 'applied' -> 0, 'error' -> 'cancelled', 'epoch' -> global_epoch});
      global_pending = null
    );
    return({'ok' -> true})
  );
  if(action == 'batch',
    prior = _read('results/'+id);
    if(prior != null, return(prior));
    if(global_pending != null, throw('worker_busy'));
    if(length(req:'operations') > 20, throw('batch_too_large'));
    if(req:'dry_run',
      if(length(req:'operations') != 0, throw('dry_run_requires_empty_batch'));
      _dimension_policy(req:'dimension', _policy())
    );
    if(!req:'dry_run', _permit(req));
    global_pending = id;
    schedule(4, '_apply', id, req);
    return({'ok' -> true, 'queued' -> true, 'request_id' -> id, 'epoch' -> global_epoch})
  );
  throw('unknown_action')
);
_request(id) -> (
  if(!(id ~ '^[a-f0-9]{32}$'), return(print('MC_BUILDER_JSON:'+encode_json({'ok' -> false, 'error' -> 'invalid_request_id'}))));
  response = try(_dispatch(id, _read('requests/'+id)), {'ok' -> false, 'error' -> str(_)});
  print('MC_BUILDER_JSON:'+encode_json(response))
);
