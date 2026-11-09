import type {
  ItineraryAcceptedResponse,
  ItineraryCreateRequest,
  ItineraryRunResponse,
} from "./types";

export class ItineraryApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ItineraryApiError";
  }
}

interface ItineraryClient {
  create(
    payload: ItineraryCreateRequest,
    signal?: AbortSignal,
  ): Promise<ItineraryAcceptedResponse>;
  getRun(runId: string, signal?: AbortSignal): Promise<ItineraryRunResponse>;
  regenerate(
    groupId: string,
    signal?: AbortSignal,
  ): Promise<ItineraryAcceptedResponse>;
}

type ErrorBody = {
  detail?: unknown;
  error?: unknown;
};

function readErrorValue(value: unknown): { message: string; code: string | null } {
  if (typeof value === "string") return { message: value, code: null };
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    return {
      message:
        typeof record.message === "string" ? record.message : "추천 요청을 처리하지 못했습니다.",
      code: typeof record.code === "string" ? record.code : null,
    };
  }
  return { message: "추천 요청을 처리하지 못했습니다.", code: null };
}

async function readError(response: Response): Promise<ItineraryApiError> {
  try {
    const payload = (await response.json()) as ErrorBody;
    const error = readErrorValue(payload.error ?? payload.detail);
    return new ItineraryApiError(error.message, response.status, error.code);
  } catch {
    return new ItineraryApiError(
      "추천 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      response.status,
    );
  }
}

export function createItineraryClient(
  fetcher: typeof fetch = fetch,
): ItineraryClient {
  async function request<T>(
    path: string,
    method: "GET" | "POST",
    signal?: AbortSignal,
    body?: unknown,
  ): Promise<T> {
    const response = await fetcher(path, {
      method,
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal,
    });
    if (!response.ok) throw await readError(response);
    return (await response.json()) as T;
  }

  return {
    create(payload, signal) {
      return request<ItineraryAcceptedResponse>(
        "/api/itineraries",
        "POST",
        signal,
        payload,
      );
    },
    getRun(runId, signal) {
      return request<ItineraryRunResponse>(
        "/api/runs/" + encodeURIComponent(runId),
        "GET",
        signal,
      );
    },
    regenerate(groupId, signal) {
      return request<ItineraryAcceptedResponse>(
        "/api/itineraries/" + encodeURIComponent(groupId) + "/regenerate",
        "POST",
        signal,
      );
    },
  };
}

export const itineraryClient = createItineraryClient();
