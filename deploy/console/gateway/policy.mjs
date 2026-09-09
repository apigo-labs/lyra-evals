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
  if((data.tools || []).some(t => !['function','custom'].includes(t.type) && !(config.path === '/v1/messages' && !t.type && typeof t.name === 'string'))) throw Error('Managed tool forbidden');
  const chat = config.path === '/v1/chat/completions';
  const outputKey = config.path === '/v1/responses' ? 'max_output_tokens' : chat && config.model.startsWith('gpt-') ? 'max_completion_tokens' : 'max_tokens';
  const requested = data[outputKey] ?? (chat ? data.max_tokens ?? data.max_completion_tokens : undefined) ?? config.max_output_tokens;
  if(chat) {delete data.max_tokens; delete data.max_completion_tokens;}
  if (!Number.isSafeInteger(requested) || requested <= 0) throw Error('Invalid output bound');
  data[outputKey] = Math.min(requested, config.max_output_tokens);
  const upperMicros = Math.ceil((bytes + 32768) * config.input_rate + data[outputKey] * config.output_rate);
  if (!Number.isSafeInteger(upperMicros) || upperMicros <= 0 || reservedMicros + upperMicros > Math.floor(config.budget * 1e6)) throw Error('Episode budget exhausted');
  return {data, upperMicros};
}
export function parseUsage(capture) {
  const objects = [];
  try { objects.push(JSON.parse(capture)); } catch {for(const line of capture.split('\n')) if(line.startsWith('data:')) {try{objects.push(JSON.parse(line.slice(5).trim()));}catch{}}}
  let usage = {};
  for(const object of objects) {const u = object.response?.usage || object.message?.usage || object.usage; if(u) usage = {...usage, ...u};}
  return {usage: Object.keys(usage).length ? usage : null, event_types: objects.map(o => o.type).filter(Boolean)};
}
