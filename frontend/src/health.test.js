import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkHealth } from './health.ts';

test('서버가 정상 응답하면 연결 상태를 반환한다', async () => {
  const status = await checkHealth(async (url) => {
    assert.equal(url, '/api/health');
    return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
  });
  assert.equal(status, true);
});

test('서버가 실패를 반환하거나 연결할 수 없으면 연결 실패를 반환한다', async () => {
  assert.equal(await checkHealth(async () => new Response('', { status: 503 })), false);
  assert.equal(await checkHealth(async () => { throw new Error('offline'); }), false);
});
