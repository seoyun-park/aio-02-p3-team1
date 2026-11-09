import { test } from "node:test";
import assert from "node:assert/strict";
import { createTimetableClient, TimetableApiError } from "./api/timetable.ts";

const stopResponse = {
  metadata: {
    dataset_id: "timetable-test",
    timezone: "Asia/Seoul",
    source_url: "https://example.invalid/timetable",
    effective_from: "2026-08-15",
    effective_until: null,
    operating_days_status: "unknown",
    separate_operational_notices_reviewed: false,
    service_date_status: "unknown",
  },
  stops: [{ stop_id: "MANGWON", name_ko: "망원", display_order: 1 }],
};

const tripResponse = {
  metadata: stopResponse.metadata,
  departures: [
    {
      from_stop_id: "MANGWON",
      to_stop_id: "YEOUIDO",
      departure_time: "14:42",
      travel_minutes: 25,
      estimated_arrival_time: "15:07",
      arrival_time_source: "calculated",
      arrival_day_offset: 0,
      stop_call_status: "not_certified_by_this_calculation",
    },
  ],
};

test("선착장 목록은 인증 쿠키를 포함해 기존 API에서 가져온다", async () => {
  const client = createTimetableClient(async (url, init) => {
    assert.equal(url, "/api/stops");
    assert.equal(init.credentials, "include");
    return new Response(JSON.stringify(stopResponse), { status: 200 });
  });

  assert.deepEqual(await client.getStops(), stopResponse);
});

test("구간 시간표 요청은 출발·도착 선착장 ID를 쿼리로 전달한다", async () => {
  const client = createTimetableClient(async (url, init) => {
    assert.equal(
      url,
      "/api/trips?from_stop_id=MANGWON&to_stop_id=YEOUIDO",
    );
    assert.equal(init.credentials, "include");
    return new Response(JSON.stringify(tripResponse), { status: 200 });
  });

  assert.deepEqual(await client.getTrips("MANGWON", "YEOUIDO"), tripResponse);
});

test("API 오류 코드를 사용자 화면에서 구분할 수 있게 보존한다", async () => {
  const client = createTimetableClient(async () =>
    new Response(
      JSON.stringify({ detail: { code: "timetable_unavailable", message: "시간표 파일을 읽을 수 없습니다." } }),
      { status: 503 },
    ),
  );

  await assert.rejects(client.getStops(), (error) => {
    assert.ok(error instanceof TimetableApiError);
    assert.equal(error.status, 503);
    assert.equal(error.code, "timetable_unavailable");
    assert.equal(error.message, "시간표 파일을 읽을 수 없습니다.");
    return true;
  });
});
