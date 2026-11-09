from typing import Any, NotRequired, TypedDict


class ItineraryAgentState(TypedDict):
    run_id: str
    trip_context: dict[str, Any]
    candidates: list[dict[str, Any]]
    revision_count: int
    movement_review: NotRequired[dict[str, Any]]
    event_explanation: NotRequired[dict[str, Any]]
    critic_review: NotRequired[dict[str, Any]]
    final_result: NotRequired[dict[str, Any]]
