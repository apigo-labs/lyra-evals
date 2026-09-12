import test from 'node:test';
import assert from 'node:assert/strict';
import {reserveRequest,parseUsage} from './policy.mjs';
const config = {model:'test',path:'/v1/responses',max_output_tokens:100,input_rate:2,output_rate:10,budget:1};
test('closed-book profile removes harness tool declarations before sending', () => {
  const result = reserveRequest({model:'test',tools:[{type:'web_search'}],tool_choice:'auto',parallel_tool_calls:true},10,{...config,allow_tools:false},0);
  assert.equal(result.data.tools,undefined);
  assert.equal(result.data.tool_choice,undefined);
  assert.equal(result.data.parallel_tool_calls,undefined);
  assert.throws(()=>reserveRequest({model:'test',tools:[{type:'web_search'}]},10,config,0));
});
test('reject bypasses and enforce integer reservations', () => {
  for(const max_output_tokens of [-100,0,NaN,Infinity,'100']) assert.throws(()=>reserveRequest({model:'test',max_output_tokens},100,config,0));
  assert.throws(()=>reserveRequest({model:'different'},100,config,0));
  assert.throws(()=>reserveRequest({model:'test',previous_response_id:'x'},100,config,0));
  assert.throws(()=>reserveRequest({model:'test',input:[{type:'input_image'}]},100,config,0));
  assert.throws(()=>reserveRequest({model:'test',tools:[{type:'web_search'}]},100,config,0));
  assert.throws(()=>reserveRequest({model:'test'},100,config,999999));
  const r=reserveRequest({model:'test',max_output_tokens:200},100,config,0);
  assert.equal(r.data.max_output_tokens,100); assert.equal(r.upperMicros,66736);
});
test('parse both SSE forms and merge Claude usage', () => {
  assert.equal(parseUsage('data:{"type":"response.completed","response":{"usage":{"input_tokens":5,"output_tokens":2}}}\n\ndata: [DONE]').usage.output_tokens,2);
  assert.deepEqual(parseUsage('data: {"message":{"usage":{"input_tokens":4}}}\ndata: {"usage":{"output_tokens":3}}').usage,{input_tokens:4,output_tokens:3});
});

import {UsageStream} from './stream.mjs';
test('stream usage survives byte boundaries without retaining the answer', () => {
 const stream = new UsageStream();
 const bytes = new TextEncoder().encode('data: {"type":"response.output_text.delta","delta":"你好"}\n\ndata:{"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":3}}}\n\n');
 for(const byte of bytes) stream.consume(new Uint8Array([byte]));
 stream.consume(new Uint8Array(),true);
 assert.deepEqual(stream.usage,{input_tokens:7,output_tokens:3});
 assert.equal(stream.pending,'');
 assert(stream.types.has('response.completed'));
});
test('simulator chat caps canonical token bound and removes conflicting aliases',()=>{
 const chat={...config,path:'/v1/chat/completions',model:'gpt-test'};
 const result=reserveRequest({model:'gpt-test',max_tokens:1000000,max_completion_tokens:50},100,chat,0);
 assert.equal(result.data.max_completion_tokens,50);
 assert.equal(result.data.max_tokens,undefined);
 assert.throws(()=>reserveRequest({model:'gpt-test',max_completion_tokens:-1},100,chat,0));
});

import {normalizeLyra, outboundShape} from './policy.mjs';
const lyra = {model:'apigo/lyra-auto',path:'/v1/messages',max_output_tokens:4096,input_rate:3,output_rate:12,budget:2,allow_tools:false};
test('lyra target keeps evaluation semantics while dropping provider-only hints', () => {
  const body = {
    model:'apigo/lyra-auto', max_tokens:2048,
    system:[{type:'text',text:'You are Claude Code.',cache_control:{type:'ephemeral'}},{type:'text',text:'Answer exactly.'}],
    messages:[{role:'user',content:[{type:'text',text:'List three colors.',cache_control:{type:'ephemeral'}}]}],
    metadata:{user_id:'abc'}, context_management:{edits:[]}, thinking:{type:'enabled',budget_tokens:1024},
    tools:[{name:'Bash',description:'run'}], tool_choice:'auto', stream:true,
  };
  const {data, normalized} = reserveRequest(body, 500, lyra, 0);
  assert.equal(data.system, 'You are Claude Code.\n\nAnswer exactly.');
  assert.equal(data.messages[0].content[0].text, 'List three colors.');
  assert.equal(data.messages[0].content[0].cache_control, undefined);
  assert.equal(data.metadata, undefined);
  assert.equal(data.context_management, undefined);
  assert.deepEqual(data.thinking, {type:'enabled',budget_tokens:1024});
  assert.equal(data.max_tokens, 2048);
  assert.equal(data.stream, true);
  assert.deepEqual(normalized.sort(), ['cache_control','context_management','metadata','system_text_blocks_joined']);
});
test('normalization is scoped to lyra messages targets and refuses non-text system blocks', () => {
  const passthrough = {model:'claude-sonnet-5',metadata:{user_id:'abc'},system:[{type:'text',text:'hi'}]};
  assert.deepEqual(normalizeLyra(passthrough, {...lyra, model:'claude-sonnet-5'}), []);
  assert.deepEqual(passthrough.metadata, {user_id:'abc'});
  assert.deepEqual(passthrough.system, [{type:'text',text:'hi'}]);
  assert.deepEqual(normalizeLyra({metadata:{}}, {...lyra, path:'/v1/responses'}), []);
  assert.throws(()=>normalizeLyra({system:[{type:'image'}]}, lyra), /refusing to alter evaluation input/);
});
test('outbound shape records parameter names without any prompt text', () => {
  const shape = outboundShape({model:'apigo/lyra-auto', system:'a'.repeat(9000), max_tokens:2048, stream:true,
    messages:[{role:'user',content:[{type:'text',text:'secret question'}]}], samples:[1,2,3,4,5,6,7,8]});
  assert.equal(shape.model, 'apigo/lyra-auto');
  assert.equal(shape.system, '<string:9000>');
  assert.equal(shape.max_tokens, 2048);
  assert.equal(shape.messages[0].content[0].type, 'text');
  assert.equal(shape.messages[0].content[0].text, '<string:15>');
  assert.equal(shape.samples[6], '+2');
  assert(!JSON.stringify(shape).includes('secret'));
});
