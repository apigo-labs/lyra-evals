// Incremental SSE parser: bounded memory and linear work, including split UTF-8 chunks.
export class UsageStream {
  constructor() { this.decoder = new TextDecoder(); this.pending = ''; this.usage = null; this.types = new Set(); }
  consume(chunk, final = false) {
    this.pending += this.decoder.decode(chunk, {stream: !final});
    if (this.pending.length > 2000000) throw Error('Oversized SSE event');
    const lines = this.pending.split('\n'); this.pending = final ? '' : lines.pop();
    if(final && this.pending) lines.push(this.pending);
    let changed = false;
    for(const line of lines) {
      if (!line.startsWith('data:')) continue;
      try {
        const item = JSON.parse(line.slice(5).trim());
        if(item.type) this.types.add(item.type);
        const usage = item.response?.usage || item.message?.usage || item.usage;
        if(usage) {this.usage = {...this.usage, ...usage}; changed = true;}
      } catch { /* Non-JSON heartbeat or [DONE]. */ }
    }
    return changed;
  }
}
