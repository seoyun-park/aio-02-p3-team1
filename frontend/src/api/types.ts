export interface Sailing {
  id: string;
  departure: string;
  arrival: string;
  originStopId?: string;
  destinationStopId?: string;
  travelMinutes?: number;
  stopCallStatus?: string;
}

export interface Exhibition {
  id: string;
  title: string;
  category: string;
  place: string;
  reason: string;
  travelMinutes: number | null;
  visitMinutes: number | null;
  totalMinutes: number | null;
  admission: string;
  fee: string;
  collectedAt: string | null;
  reviewedAt: string | null;
  startAt?: string | null;
  endAt?: string | null;
  sourceUrl?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export type PreviewScenario =
  | "success"
  | "empty"
  | "alternative"
  | "limited"
  | "api-error"
  | "db-error"
  | "expired";

export interface RecommendationRequest {
  sailing: Sailing;
  companionType: CompanionType;
  activityType: ActivityType;
}

export interface BackendItineraryItem {
  sequence_no: number;
  item_type: "FERRY" | "EVENT" | "PLACE" | "WALK";
  title: string;
  start_at: string | null;
  end_at: string | null;
  reason: string;
  sources: Array<{ type: string; title: string; url: string | null }>;
  location: { latitude: number; longitude: number } | null;
  place_name: string | null;
  address: string | null;
  departure_stop: { stop_id: string; name: string } | null;
  arrival_stop: { stop_id: string; name: string } | null;
}

export interface BackendItinerary {
  id: string;
  title: string;
  items: BackendItineraryItem[];
}

export type CompanionType = "family" | "couple" | "friends" | "solo";
export type ActivityType =
  | "exhibition_performance"
  | "culture_event"
  | "walk_healing"
  | "food_cafe";

export type RecommendationResult =
  | { status: "success" | "limited"; events: Exhibition[]; itinerary?: BackendItinerary }
  | { status: "empty"; code?: string; message?: string }
  | { status: "alternative"; sailing: Sailing; events: Exhibition[] }
  | { status: "unavailable"; reason: "api" | "db" | "expired"; code?: string; message?: string };

export interface RecommendationClient {
  recommend(
    request: RecommendationRequest,
    signal: AbortSignal,
  ): Promise<RecommendationResult>;
}

export interface Stop {
  stop_id: string;
  name_ko: string;
  display_order: number;
}

export interface TimetableMetadata {
  dataset_id: string;
  timezone: string;
  source_url: string;
  effective_from: string;
  effective_until: string | null;
  operating_days_status: string;
  separate_operational_notices_reviewed: boolean;
  operation_policy_confirmed: boolean;
  weather_disruption_notice: string;
  service_date_status: string;
}

export interface StopListResponse {
  metadata: TimetableMetadata;
  stops: Stop[];
}

export interface TripDeparture {
  from_stop_id: string;
  to_stop_id: string;
  departure_time: string;
  travel_minutes: number;
  estimated_arrival_time: string;
  arrival_time_source: "calculated";
  arrival_day_offset: number;
  stop_call_status: string;
}

export interface TripQueryResponse {
  metadata: TimetableMetadata;
  departures: TripDeparture[];
}

export type ItineraryRunStatus = "RUNNING" | "SUCCESS" | "NO_RESULT" | "FAILED";
export type ItineraryItemType = "FERRY" | "EVENT" | "PLACE" | "WALK";
export type ItinerarySourceType = "TIMETABLE" | "CULTURE_EVENT" | "PLACE_CATALOG";

export interface ItineraryCreateRequest {
  service_date: string;
  origin_stop_id: string;
  destination_stop_id: string;
  preferred_departure_time: string;
  companion_type: CompanionType;
  activity_type: ActivityType;
}

export interface ItineraryAcceptedResponse {
  group_id: string;
  run_id: string;
  generation_index: number;
  status: "RUNNING";
}

export interface ItinerarySource {
  type: ItinerarySourceType;
  title: string;
  url: string | null;
}

export interface ItineraryStopReference {
  stop_id: string;
  name: string;
}

export interface ItineraryLocation {
  latitude: number;
  longitude: number;
}

export interface ItineraryItem {
  sequence_no: number;
  item_type: ItineraryItemType;
  title: string;
  start_at: string | null;
  end_at: string | null;
  reason: string;
  sources: ItinerarySource[];
  location: ItineraryLocation | null;
  place_name: string | null;
  address: string | null;
  departure_stop: ItineraryStopReference | null;
  arrival_stop: ItineraryStopReference | null;
}

export interface Itinerary {
  id: string;
  title: string;
  items: ItineraryItem[];
}

export interface ItineraryRunResponse {
  group_id: string;
  run_id: string;
  generation_index: number;
  status: ItineraryRunStatus;
  itinerary: Itinerary | null;
  reason: { code: string; message: string } | null;
  error: { code: string; message: string; retryable: boolean } | null;
}
