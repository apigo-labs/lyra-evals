import test from 'node:test';
import assert from 'node:assert/strict';
import {reserveRequest,parseUsage} from './policy.mjs';
const config = {model:'test',path:'/v1/responses',max_output_tokens:100,input_rate:2,output_rate:10,budget:1};
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
