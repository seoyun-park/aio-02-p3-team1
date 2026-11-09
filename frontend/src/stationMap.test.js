import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HAN_RIVER_PIERS,
  applyMapSelection,
  projectToWorldPixel,
} from "./stationMap.ts";

test("공식 한강버스 시설 지도에 등록된 선착장 8곳을 좌표와 함께 제공한다", () => {
  assert.equal(HAN_RIVER_PIERS.length, 8);
  assert.deepEqual(
    HAN_RIVER_PIERS.map((pier) => pier.stopId),
    [
      "MAGOK",
      "MANGWON",
      "YEOUIDO",
      "APGUJEONG",
      "OKSU",
      "SEOUL_FOREST",
      "TTUKSEOM",
      "JAMSIL",
    ],
  );
  assert.deepEqual(
    HAN_RIVER_PIERS.find((pier) => pier.stopId === "SEOUL_FOREST"),
    {
      stopId: "SEOUL_FOREST",
      latitude: 37.537905945,
      longitude: 127.0411175617,
      address: "성동구 성수동1가 700",
    },
  );
});

test("Web Mercator 좌표는 적도·본초자오선에서 타일 원점 중심을 가리킨다", () => {
  assert.deepEqual(projectToWorldPixel({ latitude: 0, longitude: 0 }, 0), {
    x: 128,
    y: 128,
  });
});

test("지도에서 출발지를 고른 뒤 다른 선착장을 도착지로 고른다", () => {
  const origin = applyMapSelection(
    { fromStopId: "", toStopId: "" },
    "origin",
    "MAGOK",
  );
  assert.deepEqual(origin, {
    route: { fromStopId: "MAGOK", toStopId: "" },
    nextTarget: "destination",
    error: null,
  });

  const destination = applyMapSelection(
    origin.route,
    origin.nextTarget,
    "YEOUIDO",
  );
  assert.deepEqual(destination, {
    route: { fromStopId: "MAGOK", toStopId: "YEOUIDO" },
    nextTarget: "destination",
    error: null,
  });
});

test("출발 선착장과 같은 곳을 도착지로 고르면 선택을 확정하지 않는다", () => {
  const result = applyMapSelection(
    { fromStopId: "MAGOK", toStopId: "" },
    "destination",
    "MAGOK",
  );
  assert.deepEqual(result, {
    route: { fromStopId: "MAGOK", toStopId: "" },
    nextTarget: "destination",
    error: "same-stop",
  });
});
