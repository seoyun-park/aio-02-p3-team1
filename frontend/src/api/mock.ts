import { availableSailings, todayInSeoul } from "../recommendation";
import type {
  Exhibition,
  PreviewScenario,
  RecommendationClient,
  Sailing,
} from "./types";

// 화면 개발 전용 가상 자료. 실제 시간표·검수 행사·API 계약이 아니다.
export function createDemoSailings(now: Date): Sailing[] {
  const day = todayInSeoul(now);
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Seoul",
      hour: "2-digit",
      hourCycle: "h23",
    }).format(now),
  );
  return availableSailings(
    Array.from({ length: 3 }, (_, index) => hour + index + 1)
      .filter((value) => value < 24)
      .map((value) => ({
        id: `demo-${value}`,
        departure: `${day}T${String(value).padStart(2, "0")}:00:00+09:00`,
        arrival: `${day}T${String(value).padStart(2, "0")}:30:00+09:00`,
      })),
    now,
  );
}

function demoEvents(sailing: Sailing): Exhibition[] {
  const stamp = `${todayInSeoul(new Date(sailing.departure))}T09:00:00+09:00`;
  return [
    {
      id: "river",
      title: "강의 빛을 모으다",
      category: "사진 전시 · 가상",
      place: "시연 갤러리 A",
      reason:
        "한강의 빛과 풍경을 담은 사진을 둘러보는 가상의 전시입니다. 화면 구성을 확인하기 위한 예시입니다.",
      travelMinutes: 18,
      visitMinutes: 60,
      totalMinutes: 120,
      admission: "운영·입장 조건 미검수",
      fee: "요금 미확인",
      collectedAt: stamp,
      reviewedAt: stamp,
    },
    {
      id: "city",
      title: "도시의 작은 조각들",
      category: "조형 전시 · 가상",
      place: "시연 갤러리 B",
      reason:
        "도시를 소재로 한 조형 작품을 감상하는 가상의 전시입니다. 실제 방문 가능성을 보장하지 않습니다.",
      travelMinutes: 24,
      visitMinutes: 80,
      totalMinutes: 150,
      admission: "운영·입장 조건 미검수",
      fee: "요금 미확인",
      collectedAt: stamp,
      reviewedAt: stamp,
    },
    {
      id: "garden",
      title: "도시 정원의 하루",
      category: "공공 전시 · 가상",
      place: "시연 문화공간 C",
      reason:
        "도시와 정원을 주제로 한 가상의 문화행사입니다. 화면 구성을 확인하기 위한 예시입니다.",
      travelMinutes: 22,
      visitMinutes: 50,
      totalMinutes: 100,
      admission: "운영·입장 조건 미검수",
      fee: "요금 미확인",
      collectedAt: stamp,
      reviewedAt: stamp,
    },
  ];
}

export function createMockClient(
  scenario: PreviewScenario,
  rotation = 0,
): RecommendationClient {
  return {
    async recommend(request, signal) {
      await new Promise<void>((resolve, reject) => {
        if (signal.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        };
        const timer = setTimeout(() => {
          signal.removeEventListener("abort", abort);
          resolve();
        }, 850);
        signal.addEventListener("abort", abort, { once: true });
      });
      if (scenario === "api-error")
        return { status: "unavailable", reason: "api" };
      if (scenario === "db-error")
        return { status: "unavailable", reason: "db" };
      if (scenario === "expired")
        return { status: "unavailable", reason: "expired" };
      if (scenario === "empty") return { status: "empty" };
      const events = demoEvents(request.sailing);
      if (events.length === 0) return { status: "empty" };
      const rotationIndex = rotation % events.length;
      const rotatedEvents = [
        ...events.slice(rotationIndex),
        ...events.slice(0, rotationIndex),
      ];
      if (scenario === "alternative") {
        const departure = new Date(
          Date.parse(request.sailing.departure) - 60 * 60 * 1000,
        );
        if (
          departure.getTime() <= Date.now() ||
          todayInSeoul(departure) !== todayInSeoul(new Date())
        )
          return { status: "empty" };
        return {
          status: "alternative",
          sailing: {
            id: "demo-earlier",
            departure: departure.toISOString(),
            arrival: new Date(
              departure.getTime() + 30 * 60 * 1000,
            ).toISOString(),
          },
          events: rotatedEvents,
        };
      }
      return {
        status: scenario === "limited" ? "limited" : "success",
        events: rotatedEvents,
      };
    },
  };
}
