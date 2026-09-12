"""Dependency-free MCP stdio endpoint implementing the 2025-06-18 tools profile."""
import inspect
import json
import os
import sys
import builder
from dimensions import DIMENSION_PATTERN

os.umask(0o077)
TOOLS = {}


def add(name, fn, description, properties=None, required=None, readonly=False):
    TOOLS[name] = (fn, {'name':name,'description':description,
        'inputSchema':{'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False},
        'annotations':{'readOnlyHint':readonly,'destructiveHint':not readonly,'openWorldHint':False}})


POS = {'type':'array','items':{'type':'integer'},'minItems':3,'maxItems':3}
JOB = {'job_id':{'type':'string','pattern':'^[a-f0-9]{24}$'}}
DIM = {'type':'string','pattern':DIMENSION_PATTERN,'description':'Namespaced ID enabled in the current dimension policy. Check server_status.'}
add('server_status',builder.health,'Read versions, live dimension heights, configured dimensions, load and construction policy.',readonly=True)
add('search_blocks',builder.search_blocks,'Search the live modded block registry; returns at most 30 names.',
    {'query':{'type':'string','minLength':1,'maxLength':80}},['query'],True)
add('describe_blocks',builder.describe_blocks,'Read complete vanilla block states from the registry without placing or loading chunks.',
    {'blocks':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':16}},['blocks'],True)
add('read_entities',builder.read_entities,'Read loaded entities in a bounded area. Modded contraption render bounds require separate inspection.',
    {'minimum':POS,'maximum':POS,'dim':DIM},['minimum','maximum','dim'],True)
add('read_blocks',builder.read_positions,'Read up to 64 loaded positions; never loads missing chunks.',
    {'dim':DIM,'positions':{'type':'array','items':POS,'maxItems':64}},['dim','positions'],True)
add('inspect_area',builder.inspect_area,'Summarize a loaded cuboid, at most 4096 positions. Full data stays on disk.',
    {'minimum':POS,'maximum':POS,'dim':DIM},['minimum','maximum'],True)
add('preview_build',builder.preview_build,'Validate a local JSON blueprint and return geometry/material summary without world changes.',
    {'blueprint_path':{'type':'string'}},['blueprint_path'])
add('prepare_build',builder.prepare_build,'Snapshot a previewed job in its configured area. Source-water replacement requires explicit plan and site opt-in; protected features cannot be changed.',JOB,['job_id'])
add('start_build',builder.start_build,'Start or resume a prepared job. Placement remains disabled until an area is configured.',JOB,['job_id'])
add('build_status',builder.build_status,'Read saved progress or error for a job.',JOB,['job_id'],True)
add('pause_build',builder.pause_build,'Pause at the next batch boundary; at most 20 additional placements may complete.',JOB,['job_id'])
add('cancel_build',builder.cancel_build,'Stop at the next batch boundary and retain already placed blocks.',JOB,['job_id'])
add('rollback_build',builder.rollback_build,'Restore directly changed air or explicitly enabled source-water positions, skipping conflicting player changes. Runs in the background.',JOB,['job_id'])


def handle(message, initialized):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}},initialized
    rid=message.get('id'); method=message['method']; params=message.get('params',{})
    if 'id' not in message:
        return None,initialized
    answer={'jsonrpc':'2.0','id':rid}
    try:
        if not isinstance(params,dict):
            raise ValueError('Parameters must be an object')
        if method=='initialize':
            supported=('2025-06-18','2025-03-26','2024-11-05')
            version=params.get('protocolVersion')
            answer['result']={'protocolVersion':version if version in supported else supported[0],
                'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'mc-builder','version':builder.VERSION},
                'instructions':'Use the locally configured Minecraft server. Read the live policy before building in its selected area. Keep blueprints in the local plans directory. Never expose credentials.'}
            initialized=True
        elif method=='ping': answer['result']={}
        elif not initialized:
            answer['error']={'code':-32000,'message':'Initialize first'}
        elif method=='tools/list': answer['result']={'tools':[entry[1] for entry in TOOLS.values()]}
        elif method=='tools/call':
            name=params.get('name'); args=params.get('arguments',{})
            if name not in TOOLS:
                answer['error']={'code':-32602,'message':'Unknown tool'}
            else:
                try:
                    if not isinstance(args,dict): raise ValueError('Arguments must be an object')
                    fn=TOOLS[name][0]
                    inspect.signature(fn).bind(**args)
                    data=fn(**args)
                    answer['result']={'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'isError':False}
                except Exception as error:
                    answer['result']={'content':[{'type':'text','text':str(error)[:1000]}],'isError':True}
        else: answer['error']={'code':-32601,'message':'Method not found'}
    except (TypeError,ValueError) as error:
        answer['error']={'code':-32602,'message':str(error)[:200]}
    return answer,initialized


def main():
    initialized=False
    for line in sys.stdin:
        try:
            if len(line)>2*1024*1024: raise ValueError('Request too large')
            message=json.loads(line)
            response,initialized=handle(message,initialized)
        except (ValueError,json.JSONDecodeError):
            response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON message'}}
        if response is not None:
            print(json.dumps(response,ensure_ascii=False,separators=(',',':')),flush=True)


if __name__=='__main__': main()
