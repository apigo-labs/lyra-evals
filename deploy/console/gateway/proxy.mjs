// A model-specific HTTP relay. It holds the provider key; agent containers never do.
import http from 'node:http';
import fs from 'node:fs';
import {reserveRequest, parseUsage, outboundShape} from './policy.mjs';
import {UsageStream} from './stream.mjs';
import {sawTerminalEvent, statusForResponse, statusForError} from './status.mjs';
import {once} from 'node:events';
const config = JSON.parse(fs.readFileSync('/config/relay.json', 'utf8'));
const key = fs.readFileSync('/config/key', 'utf8').trim();
const records = [];
let reservedMicros = 0;
function save() { fs.writeFileSync('/ledger/requests.tmp', JSON.stringify({reserved_usd: reservedMicros / 1e6, requests: records})); fs.renameSync('/ledger/requests.tmp', '/ledger/requests.json'); }
save();
http.createServer(async (req, res) => {
  const fail = (code, message) => {res.writeHead(code, {'content-type':'application/json'}); res.end(JSON.stringify({error:{message,type:'relay_policy_error'}}));};
  if(req.method !== 'POST' || new URL(req.url, 'http://gateway').pathname !== config.path) return fail(403,'Endpoint denied');
  let body = Buffer.alloc(0);
  for await (const chunk of req) {body = Buffer.concat([body, chunk]); if(body.length > 2000000) return fail(413,'Request too large');}
  let data;
  try {data = JSON.parse(body);} catch {return fail(400,'Invalid JSON');}
  let reservation;
  try { reservation = reserveRequest(data, body.length, config, reservedMicros); }
  catch (error) { return fail(error.message.includes('budget') ? 402 : 403, error.message); }
  data = reservation.data;
  reservedMicros += reservation.upperMicros;
  const upper = reservation.upperMicros / 1e6;
  const record = {model:config.model,index:records.length, reserved_usd:upper, status:'pending', usage:null, estimated_cost_usd:null, request_id:null, client_error:null, normalized:reservation.normalized, outbound:outboundShape(data)};
  records.push(record); save();
  const started = Date.now();
  const disconnected = new AbortController();
  let clientDisconnected = false;
  res.on('close', () => {if(!res.writableEnded) {clientDisconnected = true; disconnected.abort();}});
  const deadline = AbortSignal.timeout(config.deadline_seconds * 1000);
  const signal = AbortSignal.any([disconnected.signal, deadline]);
  let completedSeen = false;
  try {
    const response = await fetch(config.endpoint + config.path, {method:'POST', redirect:'error', signal, headers:{'content-type':'application/json','authorization':`Bearer ${key}`,'x-api-key':key,'anthropic-version':'2023-06-01',...(req.headers['anthropic-beta'] ? {'anthropic-beta':req.headers['anthropic-beta']} : {})}, body:JSON.stringify(data)});
    record.request_id = response.headers.get('x-request-id') || response.headers.get('request-id');
    record.status = 'receiving'; save();
    res.writeHead(response.status, {'content-type':response.headers.get('content-type') || 'application/json'});
    let capture = '';
    const stream = new UsageStream();
    const isStream = (response.headers.get('content-type') || '').includes('text/event-stream');
    function updateUsage() {
      const parsed = isStream ? {usage: stream.usage, event_types:[...stream.types]} : parseUsage(capture);
      record.event_types = [...new Set(parsed.event_types)];
      record.usage = parsed.usage;
      const usage = parsed.usage || {};
      const input = usage.input_tokens ?? usage.prompt_tokens;
      const output = usage.output_tokens ?? usage.completion_tokens;
      if(Number.isFinite(input) && Number.isFinite(output)) record.estimated_cost_usd = ((input + (usage.cache_creation_input_tokens || 0) + (usage.cache_read_input_tokens || 0)) * config.input_rate + output * config.output_rate) / 1000000;
      record.duration_ms = Date.now() - started;
      if(sawTerminalEvent(parsed.event_types)) {completedSeen = true; record.status = 'received';}
      save();
    }
    for await(const part of response.body) {
      if(isStream) { if(stream.consume(part)) updateUsage(); }
      else {capture += Buffer.from(part).toString('utf8'); if(capture.length > 16000000) throw Error('Response too large');}
      if(!res.write(part)) await once(res, 'drain', {signal});
    }
    if(isStream) stream.consume(new Uint8Array(), true);
    updateUsage();
    // Non-stream completion: response.ok plus the body having been fully read counts as completed.
    if(!isStream && response.ok) completedSeen = true;
    res.end();
    record.status = statusForResponse({completed: completedSeen, responseOk: response.ok, httpStatus: response.status});
  } catch {
    const reason = clientDisconnected ? 'client_disconnected' : deadline.aborted ? 'deadline' : 'client_write_failed';
    const {status, client_error} = statusForError({completed: completedSeen, reason});
    record.status = status;
    record.client_error = client_error;
    if(!res.headersSent) fail(502,'Gateway transport failed'); else res.end();
  }
  finally {record.duration_ms = Date.now() - started; save();}
}).listen(8080, '0.0.0.0');
