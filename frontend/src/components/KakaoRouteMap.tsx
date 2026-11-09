import { useEffect, useMemo, useRef, useState } from "react";
import type { ItineraryItem, Sailing } from "../api/types";
import { HAN_RIVER_PIERS } from "../stationMap";

const KAKAO_SDK_URL = "https://dapi.kakao.com/v2/maps/sdk.js";

type RoutePoint = {
  id: string;
  label: string;
  latitude: number;
  longitude: number;
};

type KakaoOverlayHandle = {
  setMap(map: unknown): void;
};

let kakaoSdkPromise: Promise<void> | null = null;

function loadKakaoSdk(appKey: string): Promise<void> {
  if (window.kakao?.maps) return Promise.resolve();
  if (kakaoSdkPromise) return kakaoSdkPromise;

  kakaoSdkPromise = new Promise<void>((resolve, reject) => {
    const existingScript = document.querySelector<HTMLScriptElement>(
      "script[data-kakao-maps-sdk]",
    );
    const script = existingScript ?? document.createElement("script");
    script.dataset.kakaoMapsSdk = "true";
    script.async = true;
    script.src = `${KAKAO_SDK_URL}?appkey=${encodeURIComponent(appKey)}&autoload=false`;
    script.onload = () => {
      if (!window.kakao?.maps) {
        reject(new Error("Kakao Maps SDK를 초기화하지 못했습니다."));
        return;
      }
      window.kakao.maps.load(resolve);
    };
    script.onerror = () => reject(new Error("Kakao Maps SDK를 불러오지 못했습니다."));
    if (!existingScript) document.head.appendChild(script);
  });

  return kakaoSdkPromise;
}

function getRoutePoints(items: ItineraryItem[], sailing: Sailing): RoutePoint[] {
  const points: RoutePoint[] = [];
  const addPoint = (point: RoutePoint | null) => {
    if (point && !points.some((candidate) => candidate.id === point.id)) {
      points.push(point);
    }
  };

  const ferryItem = items.find((item) => item.item_type === "FERRY");
  const departureStopId = ferryItem?.departure_stop?.stop_id ?? sailing.originStopId;
  const arrivalStopId = ferryItem?.arrival_stop?.stop_id ?? sailing.destinationStopId;
  const departurePier = HAN_RIVER_PIERS.find((pier) => pier.stopId === departureStopId);
  const arrivalPier = HAN_RIVER_PIERS.find((pier) => pier.stopId === arrivalStopId);

  addPoint(
    departurePier
      ? {
          id: departurePier.stopId,
          label: `${ferryItem?.departure_stop?.name ?? departurePier.stopId} 출발`,
          latitude: departurePier.latitude,
          longitude: departurePier.longitude,
        }
      : null,
  );
  addPoint(
    arrivalPier
      ? {
          id: arrivalPier.stopId,
          label: `${ferryItem?.arrival_stop?.name ?? arrivalPier.stopId} 도착 예정`,
          latitude: arrivalPier.latitude,
          longitude: arrivalPier.longitude,
        }
      : null,
  );

  items
    .filter((item) => item.item_type !== "FERRY" && item.location !== null)
    .forEach((item) => {
      if (!item.location) return;
      addPoint({
        id: `item-${item.sequence_no}`,
        label: item.title,
        latitude: item.location.latitude,
        longitude: item.location.longitude,
      });
    });

  return points;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

export function KakaoRouteMap({
  items,
  sailing,
}: {
  items: ItineraryItem[];
  sailing: Sailing;
}) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const overlaysRef = useRef<KakaoOverlayHandle[]>([]);
  const [sdkError, setSdkError] = useState<string | null>(null);
  const routePoints = useMemo(() => getRoutePoints(items, sailing), [items, sailing]);
  const viteEnv = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env;
  const appKey = viteEnv?.VITE_KAKAO_MAP_JS_KEY?.trim() ?? "";

  useEffect(() => {
    if (!appKey || !mapContainerRef.current || routePoints.length === 0) return;

    let cancelled = false;
    void loadKakaoSdk(appKey)
      .then(() => {
        if (cancelled || !mapContainerRef.current || !window.kakao?.maps) return;

        const { maps } = window.kakao;
        const first = routePoints[0];
        const map = new maps.Map(mapContainerRef.current, {
          center: new maps.LatLng(first.latitude, first.longitude),
          level: 8,
        });
        const bounds = new maps.LatLngBounds();
        const markers: KakaoOverlayHandle[] = [];

        routePoints.forEach((point) => {
          const position = new maps.LatLng(point.latitude, point.longitude);
          bounds.extend(position);
          const marker = new maps.Marker({ map, position, title: point.label });
          const infoWindow = new maps.InfoWindow({
            content: `<div class="kakao-map-label">${escapeHtml(point.label)}</div>`,
            removable: true,
          });
          infoWindow.open(map, marker);
          markers.push(marker);
        });

        if (routePoints.length > 1) {
          const polyline = new maps.Polyline({
            map,
            path: routePoints.map(
              (point) => new maps.LatLng(point.latitude, point.longitude),
            ),
            strokeWeight: 5,
            strokeColor: "#4338ca",
            strokeOpacity: 0.9,
            strokeStyle: "solid",
          });
          markers.push(polyline);
        }

        map.setBounds(bounds);
        overlaysRef.current.forEach((overlay) => overlay.setMap(null));
        overlaysRef.current = markers;
        setSdkError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setSdkError(
            error instanceof Error
              ? error.message
              : "Kakao Maps를 초기화하지 못했습니다.",
          );
        }
      });

    return () => {
      cancelled = true;
      overlaysRef.current.forEach((overlay) => overlay.setMap(null));
      overlaysRef.current = [];
    };
  }, [appKey, routePoints]);

  return (
    <section className="kakao-route-map" aria-labelledby="kakao-route-map-title">
      <div className="kakao-route-map-heading">
        <div>
          <p className="eyebrow">KAKAO MAP</p>
          <h3 id="kakao-route-map-title">추천 코스 경로</h3>
        </div>
        <span className="kakao-route-map-badge">좌표 기반 경로</span>
      </div>
      {!appKey ? (
        <p className="kakao-route-map-notice" role="status">
          카카오맵 JavaScript 키가 설정되지 않아 지도 대신 일정 정보만 표시합니다.
        </p>
      ) : routePoints.length === 0 ? (
        <p className="kakao-route-map-notice" role="status">
          표시할 좌표가 있는 선착장·장소 정보가 없습니다.
        </p>
      ) : sdkError ? (
        <p className="kakao-route-map-notice" role="alert">
          카카오맵을 불러오지 못했습니다. 지도 키와 등록 도메인을 확인해 주세요.
        </p>
      ) : (
        <div
          ref={mapContainerRef}
          className="kakao-route-map-canvas"
          aria-label="추천 코스 카카오맵"
        />
      )}
      <p className="kakao-route-map-footnote">
        지도 선은 추천 일정의 좌표를 연결한 표시이며 실제 도보·수상 이동 경로와 다를 수 있습니다.
      </p>
    </section>
  );
}
