import { test } from "node:test";
import assert from "node:assert/strict";
import { createMockClient } from "./api/mock.ts";
import {
  todayInSeoul,
  availableSailings,
} from "./recommendation.ts";

test("한국 자정 이후에는 UTC 전날을 오늘로 사용하지 않는다", () => {
  assert.equal(todayInSeoul(new Date("2026-11-05T16:00:00Z")), "2026-11-06");
});
test("이미 출발했거나 출발 시각이 지난 회차는 선택 목록에서 제외한다", () => {
  const items = [
    { id: "past", departure: "2026-11-05T13:00:00+09:00" },
    { id: "now", departure: "2026-11-05T14:00:00+09:00" },
    { id: "future", departure: "2026-11-05T15:00:00+09:00" },
  ];
  assert.deepEqual(
    availableSailings(items, new Date("2026-11-05T14:00:00+09:00")).map(
      (x) => x.id,
    ),
    ["future"],
  );
});
import { sailingFromTrip } from "./recommendation.ts";

test("시간표의 HH:mm과 자정 이후 일자 차이를 오늘의 KST 회차로 변환한다", () => {
  const sailing = sailingFromTrip(
    {
      from_stop_id: "JAMSIL",
      to_stop_id: "YEOUIDO",
      departure_time: "23:42",
      travel_minutes: 30,
      estimated_arrival_time: "00:12",
      arrival_time_source: "calculated",
      arrival_day_offset: 1,
      stop_call_status: "not_certified_by_this_calculation",
    },
    "2026-11-05",
  );

  assert.equal(sailing.departure, "2026-11-05T14:42:00.000Z");
  assert.equal(sailing.arrival, "2026-11-05T15:12:00.000Z");
  assert.equal(sailing.travelMinutes, 30);
});

test("Mock 재생성은 이전 추천과 다른 행사 순서를 반환한다", async () => {
  const result = await createMockClient("success", 1).recommend(
    {
      sailing: {
        id: "MANGWON-YEOUIDO-15:22",
        departure: "2026-11-05T06:22:00.000Z",
        arrival: "2026-11-05T06:48:00.000Z",
      },
      companionType: "family",
      activityType: "culture_event",
    },
    new AbortController().signal,
  );

  assert.equal(result.status, "success");
  if (result.status === "success") assert.equal(result.events[0].id, "city");
});
