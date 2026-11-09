import json
from typing import Any
from uuid import UUID

from psycopg import Connection


def save_critic_reviews(
    connection: Connection,
    run_id: UUID,
    reviews: list[dict[str, Any]],
) -> None:
    decision_map = {'PASS': 'pass', 'REVISE': 'revise', 'REJECT': 'stop'}
    for revision_number, review in enumerate(reviews):
        issues = review.get('issues') or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        sanitized_issues = [str(issue)[:1000] for issue in issues[:20]]
        revision_instructions = review.get('revision_instructions')
        if revision_instructions is not None:
            revision_instructions = str(revision_instructions)[:2000]
        connection.execute(
            """INSERT INTO itinerary_critic_reviews (
                   run_id, revision_number, decision, issues, revision_instructions
               ) VALUES (%s, %s, %s, %s::jsonb, %s)
               ON CONFLICT (run_id, revision_number) DO UPDATE SET
                   decision = EXCLUDED.decision,
                   issues = EXCLUDED.issues,
                   revision_instructions = EXCLUDED.revision_instructions,
                   recorded_at = now()""",
            (
                run_id,
                revision_number,
                decision_map.get(str(review.get('decision', 'REJECT')), 'stop'),
                json.dumps(sanitized_issues, ensure_ascii=False),
                revision_instructions,
            ),
        )
