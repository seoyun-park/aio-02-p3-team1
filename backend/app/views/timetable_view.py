from app.models.timetable import Stop, TimetableSnapshot, TripEstimate
from app.schemas.timetable import (
    StopItem,
    StopListResponse,
    TimetableMetadata,
    TripEstimateResponse,
    TripQueryResponse,
)


def build_timetable_metadata(snapshot: TimetableSnapshot) -> TimetableMetadata:
    return TimetableMetadata(
        dataset_id=snapshot.dataset_id,
        timezone=snapshot.timezone,
        source_url=snapshot.source_url,
        effective_from=snapshot.effective_from,
        effective_until=snapshot.effective_until,
        operating_days_status=snapshot.operating_days_status,
        separate_operational_notices_reviewed=(
            snapshot.separate_operational_notices_reviewed
        ),
        operation_policy_confirmed=snapshot.operation_policy_confirmed,
        weather_disruption_notice=snapshot.weather_disruption_notice,
        service_date_status='unknown',
    )


def render_stop_list_response(
    snapshot: TimetableSnapshot,
    stops: tuple[Stop, ...],
) -> StopListResponse:
    return StopListResponse(
        metadata=build_timetable_metadata(snapshot),
        stops=[
            StopItem(
                stop_id=stop.stop_id,
                name_ko=stop.name_ko,
                display_order=stop.display_order,
            )
            for stop in stops
        ],
    )


def render_trip_query_response(
    snapshot: TimetableSnapshot,
    estimates: tuple[TripEstimate, ...],
) -> TripQueryResponse:
    return TripQueryResponse(
        metadata=build_timetable_metadata(snapshot),
        departures=[
            TripEstimateResponse(
                from_stop_id=estimate.from_stop_id,
                to_stop_id=estimate.to_stop_id,
                departure_time=estimate.departure_time,
                travel_minutes=estimate.travel_minutes,
                estimated_arrival_time=estimate.estimated_arrival_time,
                arrival_time_source=estimate.arrival_time_source,
                arrival_day_offset=estimate.arrival_day_offset,
                stop_call_status=estimate.stop_call_status,
            )
            for estimate in estimates
        ],
    )
