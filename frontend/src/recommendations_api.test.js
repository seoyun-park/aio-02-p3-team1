import assert from "node:assert/strict";
import test from "node:test";
import { createRecommendationClient } from "./api/recommendations.ts";

const sailing = {
  id: "MANGWON-YEOUIDO-11:42",
  departure: "2026-11-21T02:42:00.000Z",
  arrival: "2026-11-21T03:08:00.000Z",
  originStopId: "MANGWON",
  destinationStopId: "YEOUIDO",
  travelMinutes: 26,
};

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("추천 요청을 접수하고 SUCCESS 실행 결과를 일정 항목으로 변환한다", async () => {
  const paths = [];
  const client = createRecommendationClient(
    async (path, init) => {
      paths.push([path, init?.method]);
      if (init?.method === "POST") return jsonResponse({ run_id: "run-1" }, 202);
      return jsonResponse({
        status: "SUCCESS",
        itinerary: {
          id: "version-1",
          title: "망원 출발 전시·공연 나들이",
          items: [
            {
              sequence_no: 1,
              item_type: "FERRY",
              title: "망원에서 여의도로 이동",
              start_at: "2026-11-21T11:42:00+09:00",
              end_at: "2026-11-21T12:08:00+09:00",
              reason: "시간표 기준 예상 이동",
              sources: [{ type: "TIMETABLE", title: "시간표", url: null }],
              location: null,
              place_name: null,
              address: null,
              departure_stop: { stop_id: "MANGWON", name: "망원" },
              arrival_stop: { stop_id: "YEOUIDO", name: "여의도" },
            },
            {
              sequence_no: 2,
              item_type: "EVENT",
              title: "검수된 행사",
              start_at: "2026-11-21T15:00:00+09:00",
              end_at: null,
              reason: "입력된 행사 근거에 따른 추천",
              sources: [{ type: "CULTURE_EVENT", title: "검수된 행사", url: "https://example.com/event" }],
              location: { latitude: 37.53, longitude: 126.95 },
              place_name: "용산역사박물관",
              address: null,
              departure_stop: null,
              arrival_stop: null,
            },
          ],
        },
        reason: null,
        error: null,
      });
    },
    { pollIntervalMs: 0, maxPollAttempts: 1 },
  );

  const result = await client.recommend(
    { sailing, companionType: "family", activityType: "exhibition_performance" },
    new AbortController().signal,
  );

  assert.equal(result.status, "success");
  assert.equal(result.events[0].title, "검수된 행사");
  assert.equal(result.events[0].startAt, "2026-11-21T15:00:00+09:00");
  assert.deepEqual(paths, [["/api/itineraries", "POST"], ["/api/runs/run-1", "GET"]]);
});

test("NO_RESULT 실행은 빈 결과와 백엔드 사유를 반환한다", async () => {
  const client = createRecommendationClient(
    async (_path, init) => init?.method === "POST"
      ? jsonResponse({ run_id: "run-2" }, 202)
      : jsonResponse({
        status: "NO_RESULT",
        itinerary: null,
        reason: { code: "NO_EVENTS_AFTER_ARRIVAL", message: "조건에 맞는 행사가 없습니다." },
        error: null,
      }),
    { pollIntervalMs: 0, maxPollAttempts: 1 },
  );

  const result = await client.recommend(
    { sailing, companionType: "solo", activityType: "culture_event" },
    new AbortController().signal,
  );

  assert.deepEqual(result, {
    status: "empty",
    code: "NO_EVENTS_AFTER_ARRIVAL",
    message: "조건에 맞는 행사가 없습니다.",
  });
});
