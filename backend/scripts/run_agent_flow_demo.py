import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.demo_models import create_demo_agent_models
from app.agents.llm_client import create_openai_agent_models
from app.core.database import create_database_connection
from app.services.itinerary_agent_service import run_itinerary_agent_flow


def create_demo_inputs() -> tuple[dict[str, object], list[dict[str, object]]]:
    trip_context: dict[str, object] = {
        'verification_status': 'verified',
        'from_stop_id': 'DEMO_FROM',
        'to_stop_id': 'DEMO_TO',
        'departure_time': '14:00',
        'estimated_arrival_time': '14:30',
        'evidence_ids': ['DEMO_TIMETABLE_EVIDENCE'],
    }
    candidates: list[dict[str, object]] = [
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Run the LangGraph itinerary flow with synthetic fixture data. '
            'This is not a real itinerary recommendation.'
        )
    )
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument(
        '--mock',
        action='store_true',
        help='Use deterministic local model outputs; no LLM API is called.',
    )
    model_group.add_argument(
        '--live',
        action='store_true',
        help='Call the configured OpenAI model with synthetic fixture data.',
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if os.environ.get('APP_ENV', 'development').lower() == 'production':
        raise SystemExit('The synthetic graph demo is disabled in production.')

    connection = create_database_connection(autocommit=False)
    try:
        agent_models = (
            create_openai_agent_models()
            if arguments.live
            else create_demo_agent_models()
        )
        trip_context, candidates = create_demo_inputs()
        print('DEMO ONLY: fixture route and exhibition data are synthetic.')
        print(f'Model mode: {"OpenAI" if arguments.live else "deterministic mock"}')
        print('Graph node updates:')

        def print_node_update(node_name: str, _node_update: dict[str, object]) -> None:
            print(f'  completed: {node_name}')

        result = run_itinerary_agent_flow(
            connection,
            agent_models,
            trip_context,
            candidates,
            on_node_complete=print_node_update,
        )
        print('Flow result:')
        print(json.dumps(result['final_result'], ensure_ascii=False, indent=2))
        print(f"run_id: {result['run_id']}")
        connection.rollback()
        print('Demo logs were rolled back after the flow preview.')
        return 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
