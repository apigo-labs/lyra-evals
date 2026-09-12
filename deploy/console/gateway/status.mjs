// Pure status-decision logic shared by proxy.mjs and its tests.
// Keeps "Gateway completed" separate from "delivery to the agent client failed"
// so a client-side disconnect after a real completion is never reported as
// transport_unknown (which the scheduler/billing reconciler treat as "Gateway
// never answered").

const TERMINAL_EVENT_TYPES = ['response.completed', 'response.incomplete', 'message_stop'];

export function isTerminalEventType(type) {
  return TERMINAL_EVENT_TYPES.includes(type);
}

export function sawTerminalEvent(eventTypes) {
  return (eventTypes || []).some(isTerminalEventType);
}

// Called once a Gateway response has been observed (headers received).
// `completed` is true once a terminal event has been seen (stream) or once
// the non-stream body has been fully read; `responseOk` is response.ok.
export function statusForResponse({completed, responseOk, httpStatus}) {
  if (completed) return 'received';
  return responseOk ? 'received' : `http_${httpStatus}`;
}

// Called from the catch block, i.e. the fetch/stream loop threw. `completed`
// reflects whether a terminal event had already been observed before the
// throw. `reason` is a hint about what failed downstream, e.g.
// 'client_disconnected' (res.on('close') fired before writableEnded),
// 'client_write_failed' (res.write/drain rejected), or 'deadline'
// (AbortSignal.timeout fired). Returns {status, client_error}.
export function statusForError({completed, reason}) {
  if (completed) return {status: 'received', client_error: reason || 'client_write_failed'};
  return {status: 'transport_unknown', client_error: null};
}
