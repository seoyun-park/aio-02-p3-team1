import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createItineraryClient,
  ItineraryApiError,
} from "./api/itineraries.ts";

const accepted = {
  group_id: "group-123",
  run_id: "run-456",
  generation_index: 0,
  status: "RUNNING",
};

test("추천 요청은 오늘 날짜와 선택 조건을 인증 쿠키와 함께 전송한다", async () => {
  const request = {
    service_date: "2026-11-06",
    origin_stop_id: "MANGWON",
    destination_stop_id: "YEOUIDO",
    preferred_departure_time: "14:00",
    companion_type: "family",
    activity_type: "exhibition_performance",
  };
  const client = createItineraryClient(async (url, init) => {
    assert.equal(url, "/api/itineraries");
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "include");
    assert.deepEqual(JSON.parse(init.body), request);
    return new Response(JSON.stringify(accepted), { status: 202 });
  });

  assert.deepEqual(await client.create(request), accepted);
});

test("추천 실행 상태는 run ID로 조회하고 인증 쿠키를 포함한다", async () => {
  const run = {
    group_id: accepted.group_id,
    run_id: accepted.run_id,
    generation_index: 0,
    status: "NO_RESULT",
    itinerary: null,
    reason: { code: "NO_EVENTS", message: "조건에 맞는 행사가 없습니다." },
    error: null,
  };
  const client = createItineraryClient(async (url, init) => {
    assert.equal(url, "/api/runs/run-456");
    assert.equal(init.method, "GET");
    assert.equal(init.credentials, "include");
    return new Response(JSON.stringify(run), { status: 200 });
  });

  assert.deepEqual(await client.getRun(accepted.run_id), run);
});

test("재생성은 같은 그룹 API를 호출하고 409 메시지를 보존한다", async () => {
  const client = createItineraryClient(async (url, init) => {
    assert.equal(url, "/api/itineraries/group-123/regenerate");
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "include");
    return new Response(
      JSON.stringify({
        error: {
          code: "REGENERATION_LIMIT_REACHED",
          message: "다시 추천할 수 있는 횟수를 모두 사용했습니다.",
        },
      }),
      { status: 409 },
    );
  });

  await assert.rejects(client.regenerate(accepted.group_id), (error) => {
    assert.ok(error instanceof ItineraryApiError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "REGENERATION_LIMIT_REACHED");
    assert.equal(error.message, "다시 추천할 수 있는 횟수를 모두 사용했습니다.");
    return true;
  });
});