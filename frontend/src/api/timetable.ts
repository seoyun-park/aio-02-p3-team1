import type { StopListResponse, TripQueryResponse } from "./types";

export class TimetableApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "TimetableApiError";
    this.status = status;
    this.code = code;
  }
}

interface TimetableClient {
  getStops(signal?: AbortSignal): Promise<StopListResponse>;
  getTrips(
    fromStopId: string,
    toStopId: string,
    signal?: AbortSignal,
    serviceDate?: string,
  ): Promise<TripQueryResponse>;
}

async function readError(response: Response): Promise<TimetableApiError> {
  let message = "시간표 정보를 불러오지 못했습니다.";
  let code: string | null = null;
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "detail" in payload) {
      const detail = payload.detail;
      if (typeof detail === "string") message = detail;
      if (typeof detail === "object" && detail !== null) {
        if ("message" in detail && typeof detail.message === "string")
          message = detail.message;
        if ("code" in detail && typeof detail.code === "string")
          code = detail.code;
      }
    }
  } catch {
    // Keep the safe generic message when the server did not return JSON.
  }
  return new TimetableApiError(message, response.status, code);
}

export function createTimetableClient(
  fetcher: typeof fetch = fetch,
): TimetableClient {
  async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetcher(path, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
      signal,
    });
    if (!response.ok) throw await readError(response);
    return (await response.json()) as T;
  }

  return {
    getStops(signal) {
      return request<StopListResponse>("/api/stops", signal);
    },
    getTrips(fromStopId, toStopId, signal, serviceDate) {
      const query = new URLSearchParams({
        from_stop_id: fromStopId,
        to_stop_id: toStopId,
      });
      if (serviceDate) query.set("service_date", serviceDate);
      return request<TripQueryResponse>(`/api/trips?${query.toString()}`, signal);
    },
  };
}

export const timetableClient = createTimetableClient();
