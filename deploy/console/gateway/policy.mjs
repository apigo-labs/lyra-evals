export function reserveRequest(data, bytes, config, reservedMicros) {
  if (!data || typeof data !== 'object' || data.model !== config.model) throw Error('Model differs from frozen variant');
  if(config.effort && config.effort !== 'provider_default' && (data.reasoning?.effort || data.output_config?.effort) !== config.effort) throw Error('Requested effort not serialized');
  if(data.previous_response_id || data.conversation) throw Error('Server-side context forbidden');
  function textOnly(value) {
    if(!value || typeof value !== 'object') return;
    if(['input_image','image','image_url','input_file','file','input_audio','audio'].includes(value.type)) throw Error('Only text inputs allowed');
    for (const child of Object.values(value)) textOnly(child);
  }
  textOnly(data);
  if(config.allow_tools === false) {
    delete data.tools;
    delete data.tool_choice;
    delete data.parallel_tool_calls;
  }
  if((data.tools || []).some(t => !['function','custom'].includes(t.type) && !(config.path === '/v1/messages' && !t.type && typeof t.name === 'string'))) throw Error('Managed tool forbidden');
  const chat = config.path === '/v1/chat/completions';
  const outputKey = config.path === '/v1/responses' ? 'max_output_tokens' : chat && config.model.startsWith('gpt-') ? 'max_completion_tokens' : 'max_tokens';
  const requested = data[outputKey] ?? (chat ? data.max_tokens ?? data.max_completion_tokens : undefined) ?? config.max_output_tokens;
  if(chat) {delete data.max_tokens; delete data.max_completion_tokens;}
  if (!Number.isSafeInteger(requested) || requested <= 0) throw Error('Invalid output bound');
  data[outputKey] = Math.min(requested, config.max_output_tokens);
  const normalized = normalizeLyra(data, config);
  if(config.allow_tools === false) bytes = Buffer.byteLength(JSON.stringify(data), 'utf8');
  const upperMicros = Math.ceil((bytes + 32768) * config.input_rate + data[outputKey] * config.output_rate);
  if (!Number.isSafeInteger(upperMicros) || upperMicros <= 0 || reservedMicros + upperMicros > Math.floor(config.budget * 1e6)) throw Error('Episode budget exhausted');
  return {data, upperMicros, normalized};
}

// Lyra routing models expose a subset of Anthropic Messages. Only provider hints that
// cannot change sampled output are removed here; anything affecting evaluation semantics
// (max output tokens, system prompt text, user content, thinking budget) is left intact,
// and a semantics-bearing parameter Lyra rejects must surface as a relay error instead.
export function normalizeLyra(data, config) {
  const normalized = [];
  if(!String(config.model).startsWith('apigo/lyra-') || config.path !== '/v1/messages') return normalized;
  const note = name => {if(!normalized.includes(name)) normalized.push(name);};
  if('metadata' in data) {delete data.metadata; note('metadata');}
  if('context_management' in data) {delete data.context_management; note('context_management');}
  (function strip(value) {
    if(Array.isArray(value)) return value.forEach(strip);
    if(!value || typeof value !== 'object') return;
    if('cache_control' in value) {delete value.cache_control; note('cache_control');}
    Object.values(value).forEach(strip);
  })(data);
  // Content-preserving translation: Lyra's ingress takes `system` as a plain string.
  if(Array.isArray(data.system) && data.system.length && data.system.every(b => b && b.type === 'text' && typeof b.text === 'string')) {
    data.system = data.system.map(b => b.text).join('\n\n');
    note('system_text_blocks_joined');
  }
  if(Array.isArray(data.system)) throw Error('Lyra system blocks are not plain text; refusing to alter evaluation input');
  return normalized;
}

// Prompt-free outbound shape: keys and structure only, so a Gateway rejection can be
// attributed to a parameter without ever writing prompt text or credentials to the ledger.
const LITERAL_KEYS = new Set(['type', 'role', 'model', 'stream', 'service_tier']);
export function outboundShape(value, key) {
  if(Array.isArray(value)) return value.length > 6 ? [...value.slice(0, 6).map(v => outboundShape(v)), `+${value.length - 6}`] : value.map(v => outboundShape(v));
  if(value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, outboundShape(v, k)]));
  if(typeof value === 'string') return LITERAL_KEYS.has(key) && value.length <= 64 ? value : `<string:${value.length}>`;
  return value;
}
export function parseUsage(capture) {
  const objects = [];
  try { objects.push(JSON.parse(capture)); } catch {for(const line of capture.split('\n')) if(line.startsWith('data:')) {try{objects.push(JSON.parse(line.slice(5).trim()));}catch{}}}
  let usage = {};
  for(const object of objects) {const u = object.response?.usage || object.message?.usage || object.usage; if(u) usage = {...usage, ...u};}
  return {usage: Object.keys(usage).length ? usage : null, event_types: objects.map(o => o.type).filter(Boolean)};
}
