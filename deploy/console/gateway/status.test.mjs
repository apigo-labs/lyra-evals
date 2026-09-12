import test from 'node:test';
import assert from 'node:assert/strict';
import {sawTerminalEvent, statusForResponse, statusForError} from './status.mjs';

test('sawTerminalEvent recognizes OpenAI Responses and Anthropic terminal events', () => {
  assert.equal(sawTerminalEvent(['response.created','response.completed']), true);
  assert.equal(sawTerminalEvent(['response.created','response.incomplete']), true);
  assert.equal(sawTerminalEvent(['message_start','content_block_delta','message_stop']), true);
  assert.equal(sawTerminalEvent(['response.created','response.in_progress']), false);
  assert.equal(sawTerminalEvent([]), false);
  assert.equal(sawTerminalEvent(undefined), false);
});

test('completed-then-client-disconnect: received status with client_error, not transport_unknown', () => {
  const {status, client_error} = statusForError({completed: true, reason: 'client_disconnected'});
  assert.equal(status, 'received');
  assert.equal(client_error, 'client_disconnected');
});

test('fetch throws before any completion event: transport_unknown, no client_error', () => {
  const {status, client_error} = statusForError({completed: false, reason: 'client_disconnected'});
  assert.equal(status, 'transport_unknown');
  assert.equal(client_error, null);
});

test('deadline hit after completion is recorded as a client_error of deadline, not transport_unknown', () => {
  const {status, client_error} = statusForError({completed: true, reason: 'deadline'});
  assert.equal(status, 'received');
  assert.equal(client_error, 'deadline');
});

test('write failure after completion defaults to client_write_failed', () => {
  const {status, client_error} = statusForError({completed: true, reason: undefined});
  assert.equal(status, 'received');
  assert.equal(client_error, 'client_write_failed');
});

test('http 4xx without a terminal event is recorded by status code', () => {
  const status = statusForResponse({completed: false, responseOk: false, httpStatus: 429});
  assert.equal(status, 'http_429');
});

test('completed non-stream response (response.ok, body fully read) is received', () => {
  const status = statusForResponse({completed: true, responseOk: true, httpStatus: 200});
  assert.equal(status, 'received');
});

test('non-stream response.ok without an explicit completed flag still counts as received', () => {
  // Mirrors proxy.mjs: for non-stream, completed is derived from response.ok once the body is read.
  const status = statusForResponse({completed: false, responseOk: true, httpStatus: 200});
  assert.equal(status, 'received');
});
