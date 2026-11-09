import { useMemo } from "react";
import type { PierSelectionTarget } from "../stationMap";
import { createMapLayout } from "../stationMap";
import type { Stop } from "../api/types";

const MAP_WIDTH = 1200;
const MAP_HEIGHT = 450;
const MAP_ZOOM = 12;
const MAP_CENTER = { latitude: 37.547, longitude: 126.964 };
const OFFICIAL_MAP_URL =
  "https://hangang.seoul.go.kr/www/facility/map.tab?srchCd=&opt1=&opt2=WATER&opt3=HG_BUS&opt11=HGBUS_DOCKS";

const labelSides: Record<string, string> = {
  MAGOK: "left",
  MANGWON: "top",
  YEOUIDO: "bottom",
  APGUJEONG: "left",
  OKSU: "top",
  SEOUL_FOREST: "right",
  TTUKSEOM: "bottom",
  JAMSIL: "right",
};

interface StationMapProps {
  stops: Stop[];
  stopsPhase: "loading" | "ready" | "error";
  fromStopId: string;
  toStopId: string;
  activeTarget: PierSelectionTarget;
  disabled?: boolean;
  onTargetChange: (target: PierSelectionTarget) => void;
  onSelectStop: (stopId: string) => void;
}

export function StationMap({
  stops,
  stopsPhase,
  fromStopId,
  toStopId,
  activeTarget,
  disabled = false,
  onTargetChange,
  onSelectStop,
}: StationMapProps) {
  const layout = useMemo(
    () => createMapLayout(MAP_WIDTH, MAP_HEIGHT, MAP_ZOOM, MAP_CENTER),
    [],
  );
  const availablePiers = useMemo(
    () =>
      layout.markers.flatMap((marker) => {
        const stop = stops.find((item) => item.stop_id === marker.stopId);
        return stop ? [{ ...marker, name: stop.name_ko }] : [];
      }),
    [layout.markers, stops],
  );
  const selectedOrigin = stops.find((stop) => stop.stop_id === fromStopId);
  const selectedDestination = stops.find((stop) => stop.stop_id === toStopId);
  const instruction =
    stopsPhase === "loading"
      ? "선착장 정보를 불러오고 있습니다."
      : stopsPhase === "error"
        ? "선착장 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
        : activeTarget === "origin"
          ? "지도에서 출발 선착장을 선택해 주세요."
          : "지도에서 도착 선착장을 선택해 주세요.";

  return (
    <section className="station-map-section" aria-labelledby="station-map-title">
      <div className="station-map-heading">
        <div>
          <p className="eyebrow">HAN RIVER BUS STOPS</p>
          <h3 id="station-map-title">한강 지도에서 선착장 선택</h3>
        </div>
        <a href={OFFICIAL_MAP_URL} target="_blank" rel="noreferrer">
          공식 선착장 위치 보기 ↗
        </a>
      </div>

      <div className="map-selection-controls" aria-label="선택할 선착장 종류">
        <button
          className={`map-selection-choice ${activeTarget === "origin" ? "active" : ""} ${fromStopId ? "selected-origin" : ""}`}
          type="button"
          aria-pressed={activeTarget === "origin"}
          disabled={disabled || stopsPhase !== "ready"}
          onClick={() => onTargetChange("origin")}
        >
          <span className="selection-pin origin-pin" aria-hidden="true" />
          <span>
            <small>출발 선착장</small>
            <strong>{selectedOrigin?.name_ko ?? "지도에서 선택"}</strong>
          </span>
        </button>
        <span className="map-selection-arrow" aria-hidden="true">
          →
        </span>
        <button
          className={`map-selection-choice ${activeTarget === "destination" ? "active" : ""}`}
          type="button"
          aria-pressed={activeTarget === "destination"}
          disabled={disabled || stopsPhase !== "ready"}
          onClick={() => onTargetChange("destination")}
        >
          <span className="selection-pin destination-pin" aria-hidden="true" />
          <span>
            <small>도착 선착장</small>
            <strong>{selectedDestination?.name_ko ?? "지도에서 선택"}</strong>
          </span>
        </button>
        <p className="map-selection-instruction" role="status" aria-live="polite">
          {instruction}
        </p>
      </div>

      <div className="station-map-canvas" aria-label="한강버스 선착장 지도">
        <svg
          className="station-map-tiles"
          viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
          preserveAspectRatio="none"
          role="presentation"
        >
          {layout.tiles.map((tile) => (
            <image
              key={tile.key}
              href={tile.source}
              x={tile.x}
              y={tile.y}
              width="256"
              height="256"
              aria-hidden="true"
            />
          ))}
        </svg>

        {availablePiers.map((pier) => {
          const isOrigin = pier.stopId === fromStopId;
          const isDestination = pier.stopId === toStopId;
          const selectionName = isOrigin
            ? "출발"
            : isDestination
              ? "도착"
              : "미선택";

          return (
            <button
              key={pier.stopId}
              className={`station-marker label-${labelSides[pier.stopId]} ${isOrigin ? "is-origin" : ""} ${isDestination ? "is-destination" : ""}`}
              type="button"
              style={{
                left: `${(pier.x / MAP_WIDTH) * 100}%`,
                top: `${(pier.y / MAP_HEIGHT) * 100}%`,
              }}
              aria-label={`${pier.name} 선착장, 현재 ${selectionName}`}
              aria-pressed={isOrigin || isDestination}
              disabled={disabled || stopsPhase !== "ready"}
              onClick={() => onSelectStop(pier.stopId)}
            >
              <span className="station-marker-dot" aria-hidden="true" />
              <span className="station-marker-label">{pier.name}</span>
            </button>
          );
        })}

        <a
          className="osm-attribution"
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noreferrer"
        >
          © OpenStreetMap 기여자
        </a>
      </div>

      <p className="station-map-source">
        선착장 위치는 미래한강본부 공식 시설지도에 공개된 좌표를 기준으로
        표시합니다. 지도에 노선 경로는 표시하지 않습니다.
      </p>
    </section>
  );
}
