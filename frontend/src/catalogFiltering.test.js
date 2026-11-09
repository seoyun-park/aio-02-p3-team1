import { test } from 'node:test';
import assert from 'node:assert/strict';
import { filterEventsByCategory, listEventCategories } from './catalogFiltering.ts';

const events = [
  { title: '공연 A', category: '공연' },
  { title: '전시 A', category: '전시/미술' },
  { title: '공연 B', category: '공연' },
  { title: '분류 없음', category: null },
];

test('현재 행사 페이지의 분류를 중복 없이 가나다순으로 모은다', () => {
  assert.deepEqual(listEventCategories(events), ['공연', '전시/미술']);
});

test('선택한 행사 분류만 현재 페이지 결과에 남긴다', () => {
  assert.deepEqual(filterEventsByCategory(events, '공연'), [
    { title: '공연 A', category: '공연' },
    { title: '공연 B', category: '공연' },
  ]);
  assert.equal(filterEventsByCategory(events, null).length, 4);
});
