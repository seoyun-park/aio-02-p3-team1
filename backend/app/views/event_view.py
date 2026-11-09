from app.models.event import EventCollection
from app.schemas.event import EventItemResponse, EventListResponse


def render_event_list_response(collection: EventCollection) -> EventListResponse:
    return EventListResponse(
        source_service='aiViewCulture',
        retrieved_at=collection.retrieved_at,
        total_count=collection.total_count,
        limit=collection.limit,
        offset=collection.offset,
        events=[
            EventItemResponse(
                title=event.title,
                category=event.category,
                district_name=event.district_name,
                district_code=event.district_code,
                start_date_text=event.start_date_text,
                end_date_text=event.end_date_text,
                event_date_text=event.event_date_text,
                place=event.place,
                organizer_name=event.organizer_name,
                target_audience=event.target_audience,
                free_status=event.free_status,
                fee_text=event.fee_text,
                inquiry=event.inquiry,
                player=event.player,
                program_text=event.program_text,
                description_text=event.description_text,
                organization_url=event.organization_url,
                main_image_url=event.main_image_url,
                registration_date_text=event.registration_date_text,
                ticket_text=event.ticket_text,
                theme_code=event.theme_code,
                program_time_text=event.program_time_text,
                latitude=event.latitude,
                longitude=event.longitude,
                culture_detail_url=event.culture_detail_url,
            )
            for event in collection.events
        ],
    )
