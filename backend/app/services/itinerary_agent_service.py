from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from app.agents.itinerary_graph import (
    AgentGraphNodeFailure,
    AgentNodeFailure,
    ExecutionBudgetExceeded,
    MAX_RUN_DURATION_SECONDS,
    build_itinerary_graph,
    validate_agent_inputs,
)
from app.agents.llm_client import AgentModelBundle
from app.agents.state import ItineraryAgentState
from app.services.operation_logs_service import (
    create_incident,
    finish_agent_run,
    start_agent_run,
)


def run_itinerary_agent_flow(
    connection: Connection,
    agent_models: AgentModelBundle,
    trip_context: dict[str, Any],
    candidates: list[dict[str, Any]],
    on_node_complete: Callable[[str, dict[str, Any]], None] | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    validate_agent_inputs(trip_context, candidates)
    run_started_at = perf_counter()
    run_id = run_id or start_agent_run(connection)
    initial_state: ItineraryAgentState = {
        'run_id': str(run_id),
        'trip_context': trip_context,
        'candidates': candidates,
        'revision_count': 0,
    }
    graph_state: dict[str, Any] = dict(initial_state)
    completed_nodes: list[str] = []
    critic_reviews: list[dict[str, Any]] = []

    try:
        last_step_row = connection.execute(
            "SELECT COALESCE(MAX(step_order), 0) FROM agent_step_logs WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        step_order_start = int(last_step_row[0]) if last_step_row is not None else 0
        graph = build_itinerary_graph(
            agent_models,
            connection,
            run_id,
            run_started_at=run_started_at,
            step_order_start=step_order_start,
        )
        for update in graph.stream(initial_state, stream_mode='updates'):
            for node_name, node_update in update.items():
                graph_state.update(node_update)
                completed_nodes.append(node_name)
                if node_name == 'schedule_critic_agent':
                    critic_review = node_update.get('critic_review')
                    if isinstance(critic_review, dict):
                        critic_reviews.append(dict(critic_review))
                if on_node_complete is not None:
                    on_node_complete(node_name, node_update)
    except AgentNodeFailure as error:
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LLM',
            error_code=error.error_code,
            failed_step_name=error.agent_name,
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code=error.error_code,
            message='An itinerary agent could not complete its structured output.',
        ) from error
    except AgentGraphNodeFailure as error:
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code=error.error_code,
            error_message='A deterministic LangGraph node failed.',
            failed_step_name=error.node_name,
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code=error.error_code,
            failed_step_name=error.node_name,
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code=error.error_code,
            message='A LangGraph node failed before the result could be validated.',
        ) from error
    except ExecutionBudgetExceeded as error:
        failed_step_name = error.failed_step_name or (
            completed_nodes[-1] if completed_nodes else 'graph_start'
        )
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code=error.error_code,
            error_message='The itinerary execution budget was exceeded.',
            failed_step_name=failed_step_name,
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code=error.error_code,
            failed_step_name=failed_step_name,
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code=error.error_code,
            message='The itinerary execution budget was exceeded.',
        ) from error
    except psycopg.Error as error:
        # Database failures are not retried here because the PRD has not fixed a
        # database retry policy. If the connection still works, record one
        # sanitized database incident; if it does not, preserve the safe error.
        if not connection.autocommit:
            connection.rollback()
        try:
            create_incident(
                connection,
                run_id,
                severity='CRITICAL',
                error_source='DATABASE',
                error_code='DATABASE_OPERATION_FAILED',
                error_message='A database operation failed during the itinerary run.',
                failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_start',
            )
            finish_agent_run(
                connection,
                run_id,
                'FAILED',
                error_source='DATABASE',
                error_code='DATABASE_OPERATION_FAILED',
                failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_start',
            )
        except psycopg.Error:
            pass
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code='DATABASE_OPERATION_FAILED',
            message='The database operation failed during the itinerary run.',
        ) from error
    except Exception as error:
        error_code = 'LANGGRAPH_EXECUTION_FAILED'
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code=error_code,
            error_message='The itinerary graph stopped before final validation.',
            failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_start',
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code=error_code,
            failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_start',
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code=error_code,
            message='The itinerary graph stopped before final validation.',
        ) from error

    if perf_counter() - run_started_at >= MAX_RUN_DURATION_SECONDS:
        error_code = 'RUN_TIME_LIMIT_EXCEEDED'
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code=error_code,
            error_message='The itinerary execution exceeded its time budget.',
            failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_complete',
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code=error_code,
            failed_step_name=completed_nodes[-1] if completed_nodes else 'graph_complete',
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code=error_code,
            message='The itinerary execution exceeded its time budget.',
        )

    final_result = graph_state.get('final_result')
    if not isinstance(final_result, dict):
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code='GRAPH_RESULT_MISSING',
            error_message='The itinerary graph completed without a validated result.',
            failed_step_name='result_validator',
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code='GRAPH_RESULT_MISSING',
            failed_step_name='result_validator',
        )
        raise ItineraryAgentExecutionError(
            run_id=run_id,
            error_code='GRAPH_RESULT_MISSING',
            message='The itinerary graph completed without a validated result.',
        )

    if final_result.get('result_type') == 'safe_stopped':
        create_incident(
            connection,
            run_id,
            severity='HIGH',
            error_source='LANGGRAPH',
            error_code='CRITIC_DID_NOT_APPROVE',
            error_message='The Critic did not approve the recommendation output.',
            failed_step_name='result_validator',
        )
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='LANGGRAPH',
            error_code='CRITIC_DID_NOT_APPROVE',
            failed_step_name='result_validator',
        )
    else:
        finish_agent_run(connection, run_id, 'SUCCESS')

    return {
        'run_id': run_id,
        'status': 'FAILED' if final_result.get('result_type') == 'safe_stopped' else 'SUCCESS',
        'completed_nodes': completed_nodes,
        'critic_reviews': critic_reviews,
        'final_result': final_result,
    }


class ItineraryAgentExecutionError(RuntimeError):
    def __init__(self, run_id: UUID, error_code: str, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.error_code = error_code
