import type {
  ActivityType,
  BackendItineraryItem,
  CompanionType,
  Exhibition,
  RecommendationResult,
  Sailing,
} from "./types";

type RecommendationRequest = {
  sailing: Sailing;
  companionType: CompanionType;
  activityType: ActivityType;
};

type AcceptedResponse = {
  run_id: string;
};

type RunResponse = {
  status: "RUNNING" | "SUCCESS" | "NO_RESULT" | "FAILED";
  itinerary: { id: string; title: string; items: BackendItineraryItem[] } | null;
  reason: { code: string; message: string } | null;
  error: { code: string; message: string; retryable: boolean } | null;
};

export class RecommendationApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null = null,
    public readonly retryable = false,
  ) {
    super(message);
    this.name = "RecommendationApiError";
  }
}

type ClientOptions = {
  pollIntervalMs?: number;
  maxPollAttempts?: number;
};

function toLocalDate(value: string): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function toLocalTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function toExhibition(item: BackendItineraryItem, runId: string, index: number): Exhibition {
  const source = item.sources.find((candidate) => candidate.type === "CULTURE_EVENT")
    ?? item.sources[0];
  return {
    id: `${runId}-${item.sequence_no}-${index}`,
    title: item.title,
    category: item.item_type === "EVENT" ? "문화행사" : "추천 장소",
    place: item.place_name ?? item.address ?? "장소 정보 미확인",
    reason: item.reason,
    travelMinutes: null,
    visitMinutes: null,
    totalMinutes: null,
    admission: "운영 정보는 공식 출처 확인",
    fee: "요금 정보는 공식 출처 확인",
    collectedAt: null,
    reviewedAt: null,
    startAt: item.start_at,
    endAt: item.end_at,
    sourceUrl: source?.url ?? null,
    latitude: item.location?.latitude ?? null,
    longitude: item.location?.longitude ?? null,
  };
}

function toRecommendationResult(response: RunResponse, runId: string): RecommendationResult {
  if (response.status === "NO_RESULT") {
    return {
      status: "empty",
      code: response.reason?.code ?? "NO_RESULT",
      message: response.reason?.message ?? "조건에 맞는 추천 결과가 없습니다.",
    };
  }
  if (response.status === "FAILED") {
    return {
      status: "unavailable",
      reason: response.error?.code?.includes("DATABASE") ? "db" : "api",
      code: response.error?.code ?? "ITINERARY_GENERATION_FAILED",
      message: response.error?.message ?? "추천을 생성하지 못했습니다.",
    };
  }
  if (response.status !== "SUCCESS" || response.itinerary === null) {
    throw new RecommendationApiError(
      "추천 실행이 완료되지 않았습니다.",
      502,
      "INVALID_RUN_RESPONSE",
    );
  }

  const events = response.itinerary.items
    .filter((item) => item.item_type === "EVENT" || item.item_type === "PLACE")
    .map((item, index) => toExhibition(item, runId, index));
  return {
    status: "success",
    events,
    itinerary: response.itinerary,
  };
}

export function createRecommendationClient(
  fetcher: typeof fetch = fetch,
  options: ClientOptions = {},
) {
  const pollIntervalMs = options.pollIntervalMs ?? 1000;
  const maxPollAttempts = options.maxPollAttempts ?? 90;

  async function readError(response: Response): Promise<RecommendationApiError> {
    let message = "추천 요청을 처리하지 못했습니다.";
    let code: string | null = null;
    try {
      const payload = await response.json() as { detail?: unknown; error?: { code?: string; message?: string; retryable?: boolean } };
      const detail = payload.error ?? payload.detail;
      if (typeof detail === "string") message = detail;
      if (typeof detail === "object" && detail !== null) {
        if ("message" in detail && typeof detail.message === "string") message = detail.message;
        if ("code" in detail && typeof detail.code === "string") code = detail.code;
        const retryable = "retryable" in detail && detail.retryable === true;
        return new RecommendationApiError(message, response.status, code, retryable);
      }
    } catch {
      // Keep the safe generic message when the server did not return JSON.
    }
    return new RecommendationApiError(message, response.status, code);
  }

  async function sendRequest<T>(path: string, init: RequestInit, signal: AbortSignal): Promise<T> {
    const response = await fetcher(path, {
      ...init,
      credentials: "include",
      headers: { Accept: "application/json", ...init.headers },
      signal,
    });
    if (!response.ok) throw await readError(response);
    return await response.json() as T;
  }

  async function wait(signal: AbortSignal): Promise<void> {
    if (pollIntervalMs <= 0) return;
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, pollIntervalMs);
      signal.addEventListener("abort", () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
  }

  return {
    async recommend(recommendationRequest: RecommendationRequest, signal: AbortSignal): Promise<RecommendationResult> {
      if (!recommendationRequest.sailing.originStopId || !recommendationRequest.sailing.destinationStopId) {
        throw new RecommendationApiError("출발·도착 선착장 정보가 없습니다.", 400, "ROUTE_REQUIRED");
      }
      const accepted = await sendRequest<AcceptedResponse>(
        "/api/itineraries",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            origin_stop_id: recommendationRequest.sailing.originStopId,
            destination_stop_id: recommendationRequest.sailing.destinationStopId,
            service_date: toLocalDate(recommendationRequest.sailing.departure),
            preferred_departure_time: toLocalTime(recommendationRequest.sailing.departure),
            companion_type: recommendationRequest.companionType,
            activity_type: recommendationRequest.activityType,
          }),
        },
        signal,
      );

      for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
        await wait(signal);
        const run = await sendRequest<RunResponse>(`/api/runs/${accepted.run_id}`, { method: "GET" }, signal);
        if (run.status !== "RUNNING") return toRecommendationResult(run, accepted.run_id);
      }
      throw new RecommendationApiError(
        "추천 생성 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
        408,
        "RUN_TIMEOUT",
        true,
      );
    },
  };
}

export const recommendationClient = createRecommendationClient();
