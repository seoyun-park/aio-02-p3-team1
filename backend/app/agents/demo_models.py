import json
from typing import Any

from pydantic import BaseModel

from app.agents.llm_client import AgentModelBundle
from app.agents.schemas import CandidateExplanation, CriticReview, MovementReview


class DemoChatModel:
    """Deterministic model stand-in used only by the local graph walkthrough."""

    def __init__(self, initial_failures: int = 0, failure_status: int = 503) -> None:
        self.critic_call_count = 0
        self.initial_failures_remaining = initial_failures
        self.failure_status = failure_status

    def with_structured_output(self, schema: type[BaseModel]) -> 'DemoStructuredCall':
        return DemoStructuredCall(self, schema)


class DemoStructuredCall:
    def __init__(self, model: DemoChatModel, schema: type[BaseModel]) -> None:
        self.model = model
        self.schema = schema

    def invoke(self, messages: list[tuple[str, str]]) -> BaseModel:
        if self.model.initial_failures_remaining > 0:
            self.model.initial_failures_remaining -= 1
            raise DemoLLMError(
                'Synthetic LLM service failure.',
                status_code=self.model.failure_status,
            )
        payload = json.loads(messages[-1][1])
        if self.schema is MovementReview:
            return MovementReview(
                trip_summary=(
                    f"선택 구간 {payload['trip_context']['from_stop_id']} → "
                    f"{payload['trip_context']['to_stop_id']}의 계산 도착 시각과 "
                    '검수 이동 근거를 함께 확인했습니다.'
                ),
                candidate_notes=[
                    {
                        'candidate_id': candidate.get('candidate_id', candidate.get('event_id')),
                        'note': (
                            f"검수 이동시간 {candidate['travel_minutes']}분"
                            if candidate.get('travel_minutes') is not None
                            else '입력된 후보의 출처와 분류를 확인했습니다.'
                        ),
                        'evidence_ids': candidate['evidence_ids'],
                    }
                    for candidate in payload['candidates']
                ],
                uncertainties=[],
            )

        if self.schema is CandidateExplanation:
            revision = payload.get('revision_instructions')
            recommendations = []
            for candidate in payload['candidates']:
                if candidate.get('eligibility_status') != 'POSSIBLE':
                    continue
                if candidate.get('candidate_type') == 'PLACE':
                    reason = (
                        f"{candidate['title']}은(는) 선택한 도착 선착장 근처의 "
                        '장소 카탈로그에 등록된 상설 장소입니다.'
                    )
                else:
                    reason = (
                        f"{candidate['title']}은(는) 확인된 이동시간 "
                        f"{candidate['travel_minutes']}분 조건을 충족합니다."
                    )
                if revision:
                    reason += ' 검수 근거를 바탕으로 안내합니다.'
                recommendations.append(
                    {
                        'candidate_id': candidate.get('candidate_id', candidate.get('event_id')),
                        'reason': reason,
                        'evidence_ids': candidate['evidence_ids'],
                    }
                )
            return CandidateExplanation(recommendations=recommendations[:3])

        if self.schema is CriticReview:
            self.model.critic_call_count += 1
            trip_context = payload['trip_context']
            candidate = payload['candidates'][0] if payload['candidates'] else None
            date_check = {
                'candidate_type': (candidate or {}).get('candidate_type', 'EVENT'),
                'service_date_matches': bool(trip_context.get('service_date')),
                'event_is_active_on_service_date': (
                    bool(candidate.get('event_is_active_on_service_date', True))
                    if candidate and candidate.get('candidate_type', 'EVENT') == 'EVENT'
                    else None
                ),
                'evidence': candidate.get('date_evidence', []) if candidate else [],
            }
            destination_check = {
                'arrival_stop_matches': bool(trip_context.get('to_stop_id')),
                'destination_relevance': trip_context.get(
                    'destination_relevance', 'verified'
                ),
                'evidence': trip_context.get('destination_evidence', []),
            }
            timetable_check = {
                'departure_time_matches_input': (
                    trip_context.get('departure_time')
                    == trip_context.get('requested_departure_time')
                ),
                'arrival_time_is_estimated': trip_context.get(
                    'arrival_time_source'
                ) == 'calculated',
                'actual_operation_confirmed': False,
                'evidence': trip_context.get('evidence_ids', []),
            }
            if self.model.critic_call_count == 1 and payload['candidates']:
                return CriticReview(
                    decision='REVISE',
                    date_check=date_check,
                    destination_check=destination_check,
                    timetable_check=timetable_check,
                    unsupported_claims=[],
                    issues=['추천 이유에 검수 근거 안내가 더 분명해야 합니다.'],
                    revision_instructions='검수된 이동시간과 근거를 이유에 명시하세요.',
                )
            return CriticReview(
                decision='PASS',
                date_check=date_check,
                destination_check=destination_check,
                timetable_check=timetable_check,
                unsupported_claims=[],
                issues=[],
                revision_instructions=None,
            )

        raise ValueError(f'Unsupported demo output schema: {self.schema.__name__}')


class DemoHTTPResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class DemoLLMError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = DemoHTTPResponse(status_code)


def create_demo_agent_models(
    initial_failures: int = 0,
    failure_status: int = 503,
) -> AgentModelBundle:
    return AgentModelBundle(
        chat_model=DemoChatModel(
            initial_failures=initial_failures,
            failure_status=failure_status,
        ),
        provider='demo-mock',
        model_name='deterministic-demo-fixture',
    )
