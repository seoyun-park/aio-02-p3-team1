export type PierSelectionTarget = "origin" | "destination";

export interface PierCoordinates {
  stopId: string;
  latitude: number;
  longitude: number;
  address: string;
}

export interface MapRouteSelection {
  fromStopId: string;
  toStopId: string;
}

export interface MapSelectionResult {
  route: MapRouteSelection;
  nextTarget: PierSelectionTarget;
  error: "origin-required" | "same-stop" | null;
}

export interface MapPoint {
  x: number;
  y: number;
}

export interface MapTile extends MapPoint {
  key: string;
  source: string;
}

export const HAN_RIVER_PIERS: PierCoordinates[] = [
  {
    stopId: "MAGOK",
    latitude: 37.5750775381,
    longitude: 126.844000573,
    address: "강서구 가양동 441",
  },
  {
    stopId: "MANGWON",
    latitude: 37.5546067356,
    longitude: 126.8947273394,
    address: "마포구 망원동 205-8",
  },
  {
    stopId: "YEOUIDO",
    latitude: 37.5289028719,
    longitude: 126.9343600966,
    address: "영등포구 여의도동 85-1",
  },
  {
    stopId: "APGUJEONG",
    latitude: 37.5267215261,
    longitude: 127.0164264684,
    address: "강남구 압구정동 380-1",
  },
  {
    stopId: "OKSU",
    latitude: 37.5388487971,
    longitude: 127.0178321669,
    address: "성동구 옥수동 86",
  },
  {
    stopId: "SEOUL_FOREST",
    latitude: 37.537905945,
    longitude: 127.0411175617,
    address: "성동구 성수동1가 700",
  },
  {
    stopId: "TTUKSEOM",
    latitude: 37.5289204364,
    longitude: 127.0664544782,
    address: "광진구 자양동 126-1",
  },
  {
    stopId: "JAMSIL",
    latitude: 37.5187994811,
    longitude: 127.0847479877,
    address: "송파구 잠실동 1-2",
  },
];

const TILE_SIZE = 256;
const OSM_TILE_BASE = "https://tile.openstreetmap.org";

export function projectToWorldPixel(
  point: Pick<PierCoordinates, "latitude" | "longitude">,
  zoom: number,
): MapPoint {
  const worldSize = TILE_SIZE * 2 ** zoom;
  const latitude = Math.max(-85.05112878, Math.min(85.05112878, point.latitude));
  const sinLatitude = Math.sin((latitude * Math.PI) / 180);

  return {
    x: ((point.longitude + 180) / 360) * worldSize,
    y:
      (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) *
      worldSize,
  };
}

export function createMapLayout(
  width: number,
  height: number,
  zoom: number,
  center: Pick<PierCoordinates, "latitude" | "longitude">,
) {
  const centerPixel = projectToWorldPixel(center, zoom);
  const left = centerPixel.x - width / 2;
  const top = centerPixel.y - height / 2;
  const firstTileX = Math.floor(left / TILE_SIZE);
  const firstTileY = Math.floor(top / TILE_SIZE);
  const lastTileX = Math.floor((left + width - 1) / TILE_SIZE);
  const lastTileY = Math.floor((top + height - 1) / TILE_SIZE);
  const tileCount = 2 ** zoom;
  const tiles: MapTile[] = [];

  for (let tileY = firstTileY; tileY <= lastTileY; tileY += 1) {
    for (let tileX = firstTileX; tileX <= lastTileX; tileX += 1) {
      if (tileY < 0 || tileY >= tileCount) continue;
      const normalizedX = ((tileX % tileCount) + tileCount) % tileCount;
      tiles.push({
        key: `${zoom}/${normalizedX}/${tileY}`,
        source: `${OSM_TILE_BASE}/${zoom}/${normalizedX}/${tileY}.png`,
        x: tileX * TILE_SIZE - left,
        y: tileY * TILE_SIZE - top,
      });
    }
  }

  const markers = HAN_RIVER_PIERS.map((pier) => {
    const pixel = projectToWorldPixel(pier, zoom);
    return {
      ...pier,
      x: pixel.x - left,
      y: pixel.y - top,
    };
  });

  return { width, height, tiles, markers };
}

export function applyMapSelection(
  route: MapRouteSelection,
  target: PierSelectionTarget,
  stopId: string,
): MapSelectionResult {
  if (target === "origin") {
    return {
      route: {
        fromStopId: stopId,
        toStopId: route.toStopId === stopId ? "" : route.toStopId,
      },
      nextTarget: "destination",
      error: null,
    };
  }

  if (!route.fromStopId) {
    return {
      route,
      nextTarget: "origin",
      error: "origin-required",
    };
  }

  if (stopId === route.fromStopId) {
    return { route, nextTarget: "destination", error: "same-stop" };
  }

  return {
    route: { ...route, toStopId: stopId },
    nextTarget: "destination",
    error: null,
  };
}
