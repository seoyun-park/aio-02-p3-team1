/// <reference types="vite/client" />

declare global {
  interface Window {
    kakao?: KakaoNamespace;
  }
}

interface KakaoNamespace {
  maps: KakaoMapsNamespace;
}

interface KakaoMapsNamespace {
  load(callback: () => void): void;
  LatLng: new (latitude: number, longitude: number) => KakaoLatLng;
  LatLngBounds: new () => KakaoLatLngBounds;
  Map: new (container: HTMLElement, options: KakaoMapOptions) => KakaoMap;
  Marker: new (options: KakaoMarkerOptions) => KakaoOverlay;
  InfoWindow: new (options: { content: string; removable?: boolean }) => KakaoInfoWindow;
  Polyline: new (options: KakaoPolylineOptions) => KakaoOverlay;
}

interface KakaoLatLng {
  getLat(): number;
  getLng(): number;
}

interface KakaoLatLngBounds {
  extend(position: KakaoLatLng): void;
}

interface KakaoMapOptions {
  center: KakaoLatLng;
  level: number;
}

interface KakaoMarkerOptions {
  map?: KakaoMap;
  position: KakaoLatLng;
  title?: string;
}

interface KakaoPolylineOptions {
  map?: KakaoMap;
  path: KakaoLatLng[];
  strokeWeight: number;
  strokeColor: string;
  strokeOpacity: number;
  strokeStyle: string;
}

interface KakaoMap {
  setBounds(bounds: KakaoLatLngBounds): void;
}

interface KakaoOverlay {
  setMap(map: KakaoMap | null): void;
}

interface KakaoInfoWindow {
  open(map: KakaoMap, marker: KakaoOverlay): void;
  close(): void;
}

export {};
