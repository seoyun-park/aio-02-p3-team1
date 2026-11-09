export type PlaceCategory =
  | "MUSEUM"
  | "ART_GALLERY"
  | "BOTANICAL_GARDEN"
  | "PARK"
  | "EXHIBITION_HALL"
  | "CULTURAL_SPACE";

export interface CultureEvent {
  title: string | null;
  category: string | null;
  district_name: string | null;
  district_code: string | null;
  start_date_text: string | null;
  end_date_text: string | null;
  event_date_text: string | null;
  place: string | null;
  organizer_name: string | null;
  target_audience: string | null;
  free_status: "free" | "paid_with_details" | "paid_details_missing" | "unknown";
  fee_text: string | null;
  inquiry: string | null;
  player: string | null;
  program_text: string | null;
  description_text: string | null;
  organization_url: string | null;
  main_image_url: string | null;
  registration_date_text: string | null;
  ticket_text: string | null;
  theme_code: string | null;
  program_time_text: string | null;
  latitude: number | null;
  longitude: number | null;
  culture_detail_url: string | null;
}

export interface CultureEventListResponse {
  source_service: "aiViewCulture";
  retrieved_at: string;
  total_count: number;
  limit: number;
  offset: number;
  events: CultureEvent[];
}

export interface PlaceItem {
  place_id: string;
  name: string;
  category: PlaceCategory;
  road_address: string | null;
  lot_address: string | null;
  district: string | null;
  latitude: number | null;
  longitude: number | null;
  phone: string | null;
  website: string | null;
  managing_org: string | null;
  description: string | null;
  facilities: string | null;
  opening_hours: string | null;
  closed_days: string | null;
  admission_info: string | null;
  subtype: string | null;
  source_count: number;
  primary_source_id: string;
  source_url: string | null;
  last_collected_at: string;
  last_verified_at: string | null;
  is_active: boolean;
}

export interface PlaceListResponse {
  total_count: number;
  limit: number;
  offset: number;
  places: PlaceItem[];
}

export class CatalogApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null = null,
  ) {
    super(message);
    this.name = "CatalogApiError";
  }
}

interface CatalogClient {
  getEvents(
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ): Promise<CultureEventListResponse>;
  getPlaces(
    limit: number,
    offset: number,
    signal?: AbortSignal,
    category?: PlaceCategory | null,
  ): Promise<PlaceListResponse>;
}

async function readError(response: Response): Promise<CatalogApiError> {
  let message = "목록을 불러오지 못했습니다.";
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
    // Keep the generic message when the server did not return JSON.
  }

  return new CatalogApiError(message, response.status, code);
}

export function createCatalogClient(
  fetcher: typeof fetch = fetch,
): CatalogClient {
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

  function pageQuery(limit: number, offset: number, category?: PlaceCategory | null) {
    const query = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (category) query.set("category", category);
    return query.toString();
  }

  return {
    getEvents(limit, offset, signal) {
      return request<CultureEventListResponse>(
        `/api/v1/events?${pageQuery(limit, offset)}`,
        signal,
      );
    },
    getPlaces(limit, offset, signal, category) {
      return request<PlaceListResponse>(
        `/api/v1/places?${pageQuery(limit, offset, category)}`,
        signal,
      );
    },
  };
}

export const catalogClient = createCatalogClient();
