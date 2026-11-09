import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createCatalogClient } from './api/catalogs.ts';

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

test('장소 카테고리 선택을 API category 쿼리로 전달한다', async () => {
  const requests = [];
  const client = createCatalogClient(async (input) => {
    requests.push(String(input));
    return jsonResponse({ total_count: 0, limit: 8, offset: 0, places: [] });
  });

  await client.getPlaces(8, 0, undefined, 'PARK');

  assert.equal(
    requests[0],
    '/api/v1/places?limit=8&offset=0&category=PARK',
  );
});

test('전체 장소 선택은 API category 쿼리를 보내지 않는다', async () => {
  const requests = [];
  const client = createCatalogClient(async (input) => {
    requests.push(String(input));
    return jsonResponse({ total_count: 0, limit: 8, offset: 0, places: [] });
  });

  await client.getPlaces(8, 0);

  assert.equal(requests[0], '/api/v1/places?limit=8&offset=0');
});
