import { after, afterEach, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createElement } from 'react';

// Stub the HTTP boundary, not React components or application collaborators.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
let respond;
globalThis.fetch = async (url, init) => respond(new URL(url, 'http://localhost'), init);
const { render, fireEvent, waitFor, cleanup } = await import('@testing-library/react');
const { default: App } = await import('./App.tsx');
const { AuthGate } = await import('./components/AuthGate.tsx');
const { EventCard } = await import('./components/EventCard.tsx');

const stops = [{ stop_id: 'MAGOK', name_ko: '마곡', display_order: 1 },
  { stop_id: 'MANGWON', name_ko: '망원', display_order: 2 }];
const metadata = { source_url: 'https://example.invalid/timetable', weather_disruption_notice: '시험 운항' };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
const itinerary = { id: 'fixture-itinerary', title: '합성 검증 코스', items: [{ sequence_no: 1,
  item_type: 'EVENT', title: '합성 전시', start_at: '2099-12-01T15:00:00+09:00', end_at: null,
  reason: '합성 검증 근거', sources: [], location: null, place_name: '시험 공간', address: null,
  departure_stop: null, arrival_stop: null }] };
let runStatus, generation, stopsError;
function defaultResponse(url) {
  if (url.pathname === '/api/stops') return stopsError ? json({ detail: '자료 오류' }, 503) : json({ stops, metadata });
  if (url.pathname === '/api/trips') return json({ metadata, departures: [{ from_stop_id: 'MAGOK',
    to_stop_id: 'MANGWON', departure_time: '14:00', estimated_arrival_time: '14:30',
    travel_minutes: 30, arrival_day_offset: 0 }] });
  if (url.pathname.includes('/api/runs/')) return json({ group_id: 'group', run_id: 'run',
    generation_index: generation, status: runStatus, itinerary, reason: { message: '합성 후보 없음' }, error: null });
  if (url.pathname.includes('/api/itineraries')) {
    if (url.pathname.endsWith('/regenerate')) generation += 1;
    return json({ group_id: 'group', run_id: 'run', generation_index: generation }, 202);
  }
  throw new Error(`Unexpected external request: ${url}`);
}
beforeEach(() => {
  window.history.replaceState({}, '', '/?from=MAGOK&to=MANGWON&service_date=2099-12-01&departure=14:00');
  runStatus = 'SUCCESS'; generation = 0; stopsError = false; respond = defaultResponse;
});
afterEach(cleanup);
after(() => dom.window.close());

async function openPreferences(ui) {
  fireEvent.click(ui.getByRole('button', { name: '나들이 플래너' }));
  await waitFor(() => assert.ok(ui.getByText(/시험 운항/)));
  fireEvent.click(ui.getByRole('button', { name: /배편 선택하기/ }));
  await waitFor(() => assert.ok(ui.getByLabelText(/출발 회차/)));
}

test('사용자가 선택 조건으로 추천하고 재생성·공유·인쇄를 수행한다', async () => {
  const ui = render(createElement(App, { onLogout: async () => {} }));
  await openPreferences(ui);
  fireEvent.change(ui.getByLabelText(/누구와 함께/), { target: { value: 'solo' } });
  fireEvent.change(ui.getByLabelText(/선호하는 활동/), { target: { value: 'walk_healing' } });
  fireEvent.submit(ui.container.querySelector('form'));
  await waitFor(() => assert.ok(ui.getByText('합성 검증 코스')));
  let shared = '';
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value) => { shared = value; } } });
  fireEvent.click(ui.getByRole('button', { name: /조건 공유/ }));
  await waitFor(() => assert.match(shared, /companion_type=solo/));
  fireEvent.click(ui.getByLabelText('알림 닫기'));
  let printed = false;
  window.print = () => { printed = true; };
  fireEvent.click(ui.getByRole('button', { name: /PDF 저장/ }));
  assert.equal(printed, true);
  fireEvent.click(ui.getByRole('button', { name: '다른 코스 생성' }));
  await waitFor(() => assert.match(ui.container.textContent, /추천 코스 · 2\/3/));
  fireEvent.click(ui.getByRole('button', { name: '조건 수정' }));
  fireEvent.change(ui.getByLabelText(/여행 날짜/), { target: { value: '2099-12-02' } });
  await waitFor(() => assert.equal(ui.getByLabelText(/여행 날짜/).value, '2099-12-02'));
});

test('추천 상태 조회의 일시 오류는 같은 실행을 다시 확인할 수 있다', async () => {
  let failed = false;
  respond = (url) => {
    if (url.pathname.includes('/api/runs/') && !failed) { failed = true; return json({ detail: '일시 오류' }, 503); }
    return defaultResponse(url);
  };
  const ui = render(createElement(App, { onLogout: async () => {} }));
  await openPreferences(ui);
  fireEvent.submit(ui.container.querySelector('form'));
  await waitFor(() => assert.ok(ui.getByRole('button', { name: '다시 확인' })));
  fireEvent.click(ui.getByRole('button', { name: '다시 확인' }));
  await waitFor(() => assert.ok(ui.getByText('합성 검증 코스')));
});

test('잘못된 지도 선택과 선착장 조회 실패를 안내하고 재조회한다', async () => {
  window.history.replaceState({}, '', '/');
  stopsError = true;
  const ui = render(createElement(App, { onLogout: async () => { throw new Error('network'); } }));
  fireEvent.click(ui.getByRole('button', { name: '나들이 플래너' }));
  await waitFor(() => assert.ok(ui.getByText('시간표 자료를 읽을 수 없습니다')));
  stopsError = false;
  fireEvent.click(ui.getByRole('button', { name: '다시 시도' }));
  await waitFor(() => assert.ok(ui.getByRole('button', { name: '마곡 선착장, 현재 미선택' })));
  fireEvent.click(ui.getByRole('button', { name: /배편 선택하기/ }));
  assert.ok(ui.getByText('출발과 도착 선착장을 모두 선택해 주세요.'));
  fireEvent.click(ui.getByRole('button', { name: /도착 선착장/ }));
  fireEvent.click(ui.getByRole('button', { name: '마곡 선착장, 현재 미선택' }));
  assert.ok(ui.getByText('먼저 출발 선착장을 선택해 주세요.'));
  fireEvent.click(ui.getByRole('button', { name: '마곡 선착장, 현재 미선택' }));
  fireEvent.click(ui.getByRole('button', { name: '마곡 선착장, 현재 출발' }));
  assert.ok(ui.getByText('출발지와 도착지가 같습니다. 다른 선착장을 선택해 주세요.'));
  fireEvent.click(ui.getByRole('button', { name: '망원 선착장, 현재 미선택' }));
  await waitFor(() => assert.ok(ui.getByText(/시험 운항/)));
  fireEvent.click(ui.getByRole('button', { name: '로그아웃' }));
  await waitFor(() => assert.ok(ui.getByText(/로그아웃하지 못했습니다/)));
  fireEvent.click(ui.getByRole('button', { name: '홈' }));
  fireEvent.click(ui.getByRole('link', { name: 'SEOUL WAVE 홈' }));
});

test('행사·장소 카탈로그의 분류와 페이지 이동 및 빈 결과를 표시한다', async () => {
  respond = (url) => {
    if (url.pathname === '/api/v1/events') return json({ total_count: 9, events: [{ title: '합성 축제', category: '축제',
      place: '시험 장소', start_date_text: '2099-12-01', end_date_text: '2099-12-02', free_status: 'free',
      main_image_url: 'https://example.invalid/image.png', culture_detail_url: 'https://example.invalid/event' }] });
    if (url.pathname === '/api/v1/places') return json({ total_count: 9, places: url.searchParams.has('category') ? [] : [{
      place_id: 'fixture', name: '합성 공원', category: 'PARK', district: '시험구', road_address: '시험로',
      opening_hours: '09:00', closed_days: '월요일', admission_info: '무료', website: 'https://example.invalid/park' }] });
    return defaultResponse(url);
  };
  const ui = render(createElement(App, { onLogout: async () => {} }));
  fireEvent.click(ui.getByRole('button', { name: '문화행사·장소' }));
  await waitFor(() => assert.ok(ui.getByText('합성 공원')));
  fireEvent.click(ui.getByRole('button', { name: '축제1' }));
  await waitFor(() => assert.ok(ui.getByText('합성 축제')));
  fireEvent.error(ui.container.querySelector('img'));
  assert.equal(ui.container.querySelector('img').hidden, true);
  fireEvent.click(ui.getAllByRole('button', { name: '다음 목록' })[0]);
  await waitFor(() => assert.ok(ui.getByText('2 / 2')));
  fireEvent.click(ui.getAllByRole('button', { name: '이전 목록' })[0]);
  await waitFor(() => assert.equal(ui.queryByText('2 / 2'), null));
  fireEvent.click(ui.getByRole('button', { name: '공원' }));
  await waitFor(() => assert.ok(ui.getByText(/현재 표시할 공원이/)));
});

test('인증 실패 후 비밀번호를 다시 입력하고 로그인·로그아웃한다', async () => {
  let allowLogin = false;
  respond = (url, init) => {
    assert.equal(init.credentials, 'include');
    if (url.pathname === '/api/auth/session') return json({ authenticated: false });
    if (url.pathname === '/api/auth/login') return allowLogin ? json({ authenticated: true }) : json({ detail: 'invalid' }, 401);
    if (url.pathname === '/api/auth/logout') return json({ authenticated: false });
    throw new Error('Unexpected auth request');
  };
  const ui = render(createElement(AuthGate, { children: (onLogout) => createElement('button', { onClick: onLogout }, '시험 로그아웃') }));
  await waitFor(() => assert.ok(ui.getByLabelText('비밀번호')));
  fireEvent.change(ui.getByLabelText('비밀번호'), { target: { value: 'fixture' } });
  fireEvent.submit(ui.container.querySelector('form'));
  await waitFor(() => assert.ok(ui.getByText(/비밀번호가 올바르지 않습니다/)));
  allowLogin = true;
  fireEvent.submit(ui.container.querySelector('form'));
  await waitFor(() => assert.ok(ui.getByText('시험 로그아웃')));
  fireEvent.click(ui.getByText('시험 로그아웃'));
  await waitFor(() => assert.ok(ui.getByLabelText('비밀번호')));
});

test('인증 서버 장애 후 다시 접속 상태를 확인한다', async () => {
  respond = () => { throw new Error('offline'); };
  const ui = render(createElement(AuthGate, { children: () => createElement('p', null, '인증 완료') }));
  await waitFor(() => assert.ok(ui.getByRole('button', { name: '다시 시도' })));
  respond = () => json({ authenticated: true });
  fireEvent.click(ui.getByRole('button', { name: '다시 시도' }));
  await waitFor(() => assert.ok(ui.getByText('인증 완료')));
});

test('기능 축소 행사 카드는 추정 시간을 만들지 않고 기본 정보를 표시한다', () => {
  const ui = render(createElement(EventCard, { limited: true, sailing: { departure: '2099-12-01T14:00:00+09:00' },
    event: { id: 'fixture', title: '합성 행사', category: '축제', place: '시험 장소', totalMinutes: null,
      travelMinutes: null, visitMinutes: null, startAt: null, sourceUrl: null } }));
  assert.ok(ui.getByText('행사 시간 미확인'));
  assert.ok(ui.getByText('추천 설명을 제공할 수 없어 기본 정보만 안내합니다.'));
});
