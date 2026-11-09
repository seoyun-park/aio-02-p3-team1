import json
import os
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agents.demo_models import create_demo_agent_models
from app.agents.itinerary_graph import build_itinerary_graph
from app.agents.llm_client import AgentModelBundle, LLMConfigurationError, create_openai_agent_models
from app.agents.schemas import MovementReview
from app.services import itinerary_agent_service
from app.services.itinerary_agent_service import (
    ItineraryAgentExecutionError,
    run_itinerary_agent_flow,
)


@pytest.fixture
def operation_connection():
    connection = psycopg.connect(
        host=os.environ['DATABASE_HOST'],
        port=os.environ['DATABASE_PORT'],
        dbname=os.environ['DATABASE_NAME'],
        user=os.environ['DATABASE_USER'],
        password=os.environ['DATABASE_PASSWORD'],
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def create_verified_demo_inputs():
    trip_context = {
        'verification_status': 'verified',
        'from_stop_id': 'DEMO_FROM',
        'to_stop_id': 'DEMO_TO',
        'departure_time': '14:00',
        'estimated_arrival_time': '14:30',
        'evidence_ids': ['DEMO_TIMETABLE_EVIDENCE'],
    }
    candidates = [
        {
            'event_id': 'DEMO_EVENT_001',
            'title': '합성 입력 전시 A',
            'place': '합성 입력 장소',
            'review_status': 'verified',
            'eligibility_status': 'POSSIBLE',
            'travel_minutes': 18,
            'evidence_ids': ['DEMO_REVIEW_EVIDENCE'],
        }
    ]
    return trip_context, candidates


def test_graph_recovers_from_transient_503_without_database():
    trip_context, candidates = create_verified_demo_inputs()
    models = create_demo_agent_models(initial_failures=1, failure_status=503)
    graph = build_itinerary_graph(models, connection=None)

    result = graph.invoke({
        'run_id': str(uuid4()),
        'trip_context': trip_context,
        'candidates': candidates,
        'revision_count': 0,
    })

    assert result['final_result']['result_type'] == 'eligible_candidates'
    assert models.chat_model.initial_failures_remaining == 0


def test_graph_repairs_one_malformed_structured_response_without_database():
    trip_context, candidates = create_verified_demo_inputs()

    class MalformedOnceModel:
        def __init__(self):
            self.delegate = create_demo_agent_models().chat_model
            self.malformed_response_count = 0
            self.repair_prompt_count = 0

        def with_structured_output(self, schema):
            delegate = self.delegate.with_structured_output(schema)
            model = self

            class StructuredCall:
                def invoke(self, messages):
                    if schema is MovementReview and model.malformed_response_count == 0:
                        model.malformed_response_count += 1
                        return {
                            'trip_summary': '',
                            'candidate_notes': [],
                            'uncertainties': [],
                        }
                    if schema is MovementReview:
                        payload = json.loads(messages[-1][1])
                        if 'format_correction' in payload:
                            model.repair_prompt_count += 1
                    return delegate.invoke(messages)

            return StructuredCall()

    model = MalformedOnceModel()
    graph = build_itinerary_graph(
        AgentModelBundle(
            chat_model=model,
            provider='synthetic-test',
            model_name='synthetic-format-repair',
        ),
        connection=None,
    )

    result = graph.invoke({
        'run_id': str(uuid4()),
        'trip_context': trip_context,
        'candidates': candidates,
        'revision_count': 0,
    })

    assert result['final_result']['result_type'] == 'eligible_candidates'
    assert model.malformed_response_count == 1
    assert model.repair_prompt_count == 1


def test_graph_runs_three_roles_with_one_bounded_critic_revision(operation_connection):
    trip_context, candidates = create_verified_demo_inputs()
    completed_nodes = []

    result = run_itinerary_agent_flow(
        operation_connection,
        create_demo_agent_models(),
        trip_context,
        candidates,
        on_node_complete=lambda name, _update: completed_nodes.append(name),
    )

    assert isinstance(result['run_id'], UUID)
    assert result['status'] == 'SUCCESS'
    assert result['final_result']['result_type'] == 'eligible_candidates'
    assert result['final_result']['recommendations'][0]['event_id'] == 'DEMO_EVENT_001'
    assert completed_nodes == [
        'trip_mobility_agent',
        'event_explanation_agent',
        'schedule_critic_agent',
        'event_explanation_agent',
        'schedule_critic_agent',
        'result_validator',
    ]

    run_status = operation_connection.execute(
        'SELECT status FROM agent_runs WHERE run_id = %s',
        (result['run_id'],),
    ).fetchone()[0]
    step_count = operation_connection.execute(
        'SELECT count(*) FROM agent_step_logs WHERE run_id = %s',
        (result['run_id'],),
    ).fetchone()[0]
    handoff_count = operation_connection.execute(
        'SELECT count(*) FROM handoff_logs WHERE run_id = %s',
        (result['run_id'],),
    ).fetchone()[0]
    api_call_count = operation_connection.execute(
        'SELECT count(*) FROM api_call_logs WHERE run_id = %s',
        (result['run_id'],),
    ).fetchone()[0]

    assert run_status == 'SUCCESS'
    assert step_count == 6
    assert handoff_count == 4
    assert api_call_count == 5


def test_transient_llm_failure_records_recovery_and_keeps_one_run_id(operation_connection):
    trip_context, candidates = create_verified_demo_inputs()

    result = run_itinerary_agent_flow(
        operation_connection,
        create_demo_agent_models(initial_failures=1, failure_status=503),
        trip_context,
        candidates,
    )

    run_id = result['run_id']
    run_status = operation_connection.execute(
        'SELECT status FROM agent_runs WHERE run_id = %s',
        (run_id,),
    ).fetchone()[0]
    api_call_rows = operation_connection.execute(
        '''SELECT api_call_id, run_id, success, error_source
             FROM api_call_logs WHERE run_id = %s ORDER BY called_at, api_call_id''',
        (run_id,),
    ).fetchall()
    step_run_ids = operation_connection.execute(
        'SELECT DISTINCT run_id FROM agent_step_logs WHERE run_id = %s',
        (run_id,),
    ).fetchall()
    step_names = operation_connection.execute(
        '''SELECT step_name, status FROM agent_step_logs
             WHERE run_id = %s ORDER BY step_order''',
        (run_id,),
    ).fetchall()
    handoff_run_ids = operation_connection.execute(
        'SELECT DISTINCT run_id FROM handoff_logs WHERE run_id = %s',
        (run_id,),
    ).fetchall()
    incident_rows = operation_connection.execute(
        '''SELECT incident_id, run_id, status, error_source, api_call_id
             FROM incident_logs WHERE run_id = %s''',
        (run_id,),
    ).fetchall()
    recovery_rows = operation_connection.execute(
        '''SELECT run_id, incident_id, status
             FROM recovery_logs WHERE run_id = %s''',
        (run_id,),
    ).fetchall()
    failed_api_call = next(row for row in api_call_rows if not row[2])

    assert result['status'] == 'SUCCESS'
    assert run_status == 'SUCCESS'
    assert len(api_call_rows) == 6
    assert failed_api_call[1:] == (run_id, False, 'LLM')
    assert all(row[1] == run_id for row in api_call_rows)
    assert step_run_ids == [(run_id,)]
    assert len(step_names) == 6
    assert step_names[-1] == ('result_validator', 'SUCCESS')
    assert handoff_run_ids == [(run_id,)]
    assert len(incident_rows) == 1
    incident_id, incident_run_id, incident_status, error_source, api_call_id = incident_rows[0]
    assert (incident_run_id, incident_status, error_source) == (run_id, 'RESOLVED', 'LLM')
    assert api_call_id == failed_api_call[0]
    assert recovery_rows == [(run_id, incident_id, 'RECOVERED')]


def test_permanent_llm_error_is_not_recorded_as_recovered(operation_connection):
    trip_context, candidates = create_verified_demo_inputs()

    with pytest.raises(ItineraryAgentExecutionError) as raised_error:
        run_itinerary_agent_flow(
            operation_connection,
            create_demo_agent_models(initial_failures=1, failure_status=400),
            trip_context,
            candidates,
        )

    run_id = raised_error.value.run_id
    run_row = operation_connection.execute(
        'SELECT status, error_source FROM agent_runs WHERE run_id = %s',
        (run_id,),
    ).fetchone()
    incident_row = operation_connection.execute(
        'SELECT run_id, status, error_source FROM incident_logs WHERE run_id = %s',
        (run_id,),
    ).fetchone()
    api_call_row = operation_connection.execute(
        'SELECT run_id, success, error_source FROM api_call_logs WHERE run_id = %s',
        (run_id,),
    ).fetchone()
    recovery_count = operation_connection.execute(
        'SELECT count(*) FROM recovery_logs WHERE run_id = %s',
        (run_id,),
    ).fetchone()[0]

    assert run_row == ('FAILED', 'LLM')
    assert incident_row == (run_id, 'OPEN', 'LLM')
    assert api_call_row == (run_id, False, 'LLM')
    assert recovery_count == 0


def test_langgraph_runtime_failure_is_distinct_from_llm_failure(
    operation_connection,
    monkeypatch,
):
    trip_context, candidates = create_verified_demo_inputs()

    def fail_graph_build(*_args, **_kwargs):
        raise RuntimeError('Synthetic graph construction failure.')

    monkeypatch.setattr(itinerary_agent_service, 'build_itinerary_graph', fail_graph_build)

    with pytest.raises(ItineraryAgentExecutionError) as raised_error:
        run_itinerary_agent_flow(
            operation_connection,
            create_demo_agent_models(),
            trip_context,
            candidates,
        )

    run_id = raised_error.value.run_id
    run_row = operation_connection.execute(
        'SELECT status, error_source FROM agent_runs WHERE run_id = %s',
        (run_id,),
    ).fetchone()
    incident_row = operation_connection.execute(
        'SELECT run_id, error_source FROM incident_logs WHERE run_id = %s',
        (run_id,),
    ).fetchone()
    api_call_count = operation_connection.execute(
        'SELECT count(*) FROM api_call_logs WHERE run_id = %s',
        (run_id,),
    ).fetchone()[0]

    assert run_row == ('FAILED', 'LANGGRAPH')
    assert incident_row == (run_id, 'LANGGRAPH')
    assert api_call_count == 0


def test_unreviewed_candidates_are_rejected_before_run_creation(operation_connection):
    trip_context, candidates = create_verified_demo_inputs()
    candidates[0]['review_status'] = 'candidate'
    original_run_count = operation_connection.execute(
        'SELECT count(*) FROM agent_runs'
    ).fetchone()[0]

    with pytest.raises(ValueError, match='Unreviewed candidates'):
        run_itinerary_agent_flow(
            operation_connection,
            create_demo_agent_models(),
            trip_context,
            candidates,
        )

    run_count = operation_connection.execute(
        'SELECT count(*) FROM agent_runs'
    ).fetchone()[0]
    assert run_count == original_run_count


def test_openai_model_requires_team_configured_credentials(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_MODEL', raising=False)

    with pytest.raises(LLMConfigurationError, match='OPENAI_API_KEY and OPENAI_MODEL'):
        create_openai_agent_models()
