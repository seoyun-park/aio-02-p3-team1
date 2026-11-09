import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RecommendationPage } from "./pages/RecommendationPage.tsx";

const sailing = {
  id: "MANGWON-YEOUIDO-14:00",
  departure: "2026-11-06T14:00:00+09:00",
  arrival: "2026-11-06T14:30:00+09:00",
  travelMinutes: 30,
};
const callbacks = {
  selected: sailing,
  loading: false,
  originName: "망원",
  destinationName: "여의도",
  onRetry() {},
  onRegenerate() {},
  onShare() {},
  onPrint() {},
  onSetStep() {},
};

test("완료된 코스는 백엔드 일정 항목과 근거 링크를 보여준다", () => {
  const view = {
    phase: "done",
    sailing,
    groupId: "group-123",
    run: {
      group_id: "group-123",
      run_id: "run-456",
      generation_index: 0,
      status: "SUCCESS",
      itinerary: {
        id: "itinerary-1",
        title: "한강과 전시를 즐기는 나들이",
        items: [
          {
            sequence_no: 1,
            item_type: "EVENT",
            title: "파인캐릭터 2026",
            start_at: "2026-11-06T15:00:00+09:00",
            end_at: "2026-11-06T16:00:00+09:00",
            reason: "도착 후 관람할 수 있는 행사입니다.",
            sources: [{ type: "CULTURE_EVENT", title: "서울시 행사 원문", url: "https://example.test/event" }],
            location: { latitude: 37.123, longitude: 126.456 },
            place_name: "서울 문화공간",
            address: "서울 중구 세종대로 1",
            departure_stop: null,
            arrival_stop: null,
          },
        ],
      },
      reason: null,
      error: null,
    },
  };
  const html = renderToStaticMarkup(
    createElement(RecommendationPage, { ...callbacks, view }),
  );

  assert.match(html, /한강과 전시를 즐기는 나들이/);
  assert.match(html, /파인캐릭터 2026/);
  assert.match(html, /서울 중구 세종대로 1/);
  assert.match(html, /서울시 행사 원문/);
  assert.match(html, /https:\/\/example\.test\/event/);
});

test("NO_RESULT 상태는 백엔드 안내와 재생성 동작을 보여준다", () => {
  const view = {
    phase: "done",
    sailing,
    groupId: "group-123",
    run: {
      group_id: "group-123",
      run_id: "run-456",
      generation_index: 0,
      status: "NO_RESULT",
      itinerary: null,
      reason: { code: "NO_MATCHING", message: "조건에 맞는 검수 행사가 없습니다." },
      error: null,
    },
  };
  const html = renderToStaticMarkup(
    createElement(RecommendationPage, { ...callbacks, view }),
  );

  assert.match(html, /조건에 맞는 검수 행사가 없습니다/);
  assert.match(html, /다른 코스 생성/);
});

test("FAILED 상태는 백엔드 오류 안내를 화면에 표시한다", () => {
  const view = {
    phase: "done",
    sailing,
    groupId: "group-123",
    run: {
      group_id: "group-123",
      run_id: "run-456",
      generation_index: 0,
      status: "FAILED",
      itinerary: null,
      reason: null,
      error: { code: "INPUTS_NOT_READY", message: "운항 정보와 행사 후보를 확인하지 못했습니다.", retryable: false },
    },
  };
  const html = renderToStaticMarkup(
    createElement(RecommendationPage, { ...callbacks, view }),
  );

  assert.match(html, /운항 정보와 행사 후보를 확인하지 못했습니다/);
  assert.doesNotMatch(html, /강의 빛을 모으다/);
});