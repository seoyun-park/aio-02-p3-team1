from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateMovementNote(BaseModel):
    candidate_id: str
    note: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str]


class MovementReview(BaseModel):
    trip_summary: str = Field(min_length=1, max_length=800)
    candidate_notes: list[CandidateMovementNote]
    uncertainties: list[str]


class RecommendedCandidate(BaseModel):
    candidate_id: str
    reason: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str]


class CandidateExplanation(BaseModel):
    recommendations: list[RecommendedCandidate] = Field(max_length=3)


class CriticDateCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')

    candidate_type: Literal['EVENT', 'PLACE']
    service_date_matches: bool
    event_is_active_on_service_date: bool | None
    evidence: list[str]


class CriticDestinationCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')

    arrival_stop_matches: bool
    destination_relevance: Literal['verified', 'unknown', 'mismatch']
    evidence: list[str]


class CriticTimetableCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')

    departure_time_matches_input: bool
    arrival_time_is_estimated: bool
    actual_operation_confirmed: bool
    evidence: list[str]


class CriticReview(BaseModel):
    """Strict, evidence-only contract for the schedule consistency Critic."""

    model_config = ConfigDict(extra='forbid')

    decision: Literal['PASS', 'REVISE', 'REJECT']
    date_check: CriticDateCheck
    destination_check: CriticDestinationCheck
    timetable_check: CriticTimetableCheck
    unsupported_claims: list[str]
    issues: list[str]
    revision_instructions: str | None = Field(default=None, max_length=800)
