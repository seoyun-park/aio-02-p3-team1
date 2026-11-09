# Recommendation API contract draft

This contract follows PRD v1.1 and the frontend review. It covers the user service only; admin dashboard APIs remain in the separate operations project.

## Identifiers and ownership

- `group_id` identifies one set of user-selected conditions. The initial generation and up to two user regenerations belong to the same group.
- `run_id` identifies one execution attempt. Every attempt gets a new UUID and is linked to one group and one `generation_index`.
- `generation_index` is `0` for the initial generation and `1` or `2` for user regeneration. The UI presents one version at a time.
- The signed visitor-session cookie is the ownership credential. Do not accept `visitor_session_id` in the request body. Every run lookup and regeneration must verify the run/group belongs to the cookie's active visitor session. Return `404` for a missing or foreign run/group to avoid revealing its existence.
- Successful course versions link to the producing `run_id` through `itinerary_versions.execution_id`.

## Enumerations

- `companion_type`: `family`, `couple`, `friends`, `solo`
- `activity_type`: `exhibition_performance`, `culture_event`, `walk_healing`, `food_cafe`
- `item_type`: `FERRY`, `EVENT`, `PLACE`, `WALK`
- `sources[].type`: `TIMETABLE`, `CULTURE_EVENT`, `PLACE_CATALOG`
- Public run status: `RUNNING`, `SUCCESS`, `NO_RESULT`, `FAILED`

`WALK` is included because PRD v1.1 describes walking as a schedule item. Omit it from a particular itinerary when walking is not represented as its own timeline entry.

## `POST /api/itineraries`

Creates a group and queues its initial course generation. The service date is today's date in `Asia/Seoul`; no date is sent while the UI supports today-only departures. Send the existing visitor cookie with the request.

```json
{
  "origin_stop_id": "MAGOK",
  "destination_stop_id": "YEOUIDO",
  "preferred_departure_time": "14:00",
  "companion_type": "family",
  "activity_type": "exhibition_performance"
}
```

On successful durable queue insertion, return `202 Accepted`:

```json
{
  "group_id": "00000000-0000-4000-8000-000000000001",
  "run_id": "00000000-0000-4000-8000-000000000002",
  "generation_index": 0,
  "status": "RUNNING"
}
```

## `GET /api/runs/{run_id}`

The lookup itself returns `200 OK` when the run belongs to the active visitor session, including when generation finished with `FAILED` or `NO_RESULT`.

Running:

```json
{
  "group_id": "00000000-0000-4000-8000-000000000001",
  "run_id": "00000000-0000-4000-8000-000000000002",
  "generation_index": 0,
  "status": "RUNNING",
  "itinerary": null,
  "reason": null,
  "error": null
}
```

Success:

```json
{
  "group_id": "00000000-0000-4000-8000-000000000001",
  "run_id": "00000000-0000-4000-8000-000000000002",
  "generation_index": 0,
  "status": "SUCCESS",
  "itinerary": {
    "id": "00000000-0000-4000-8000-000000000003",
    "title": "한강과 전시를 즐기는 나들이",
    "items": [
      {
        "sequence_no": 1,
        "item_type": "FERRY",
        "title": "마곡 선착장에서 여의도 선착장으로 이동",
        "start_at": "2026-11-05T14:00:00+09:00",
        "end_at": "2026-11-05T14:30:00+09:00",
        "reason": "선택한 구간의 검증된 운항 정보를 이용합니다.",
        "sources": [
          {"type": "TIMETABLE", "title": "공식 시간표", "url": "https://example.invalid/timetable"}
        ],
        "location": null,
        "place_name": null,
        "address": null,
        "departure_stop": {"stop_id": "MAGOK", "name": "마곡"},
        "arrival_stop": {"stop_id": "YEOUIDO", "name": "여의도"}
      }
    ]
  },
  "reason": null,
  "error": null
}
```

`start_at` and `end_at` use timezone-aware ISO 8601 timestamps. `location` is either `null` or `{ "latitude": number, "longitude": number }`. `place_name`, `address`, `departure_stop`, and `arrival_stop` are nullable. Ferry items provide both stop IDs and names; their route is not represented by one map point.

No suitable verified course:

```json
{
  "group_id": "00000000-0000-4000-8000-000000000001",
  "run_id": "00000000-0000-4000-8000-000000000002",
  "generation_index": 0,
  "status": "NO_RESULT",
  "itinerary": null,
  "reason": {
    "code": "NO_MATCHING_VERIFIED_COURSE",
    "message": "선택 조건에 맞는 검증된 코스를 찾지 못했습니다."
  },
  "error": null
}
```

Execution failure:

```json
{
  "group_id": "00000000-0000-4000-8000-000000000001",
  "run_id": "00000000-0000-4000-8000-000000000002",
  "generation_index": 0,
  "status": "FAILED",
  "itinerary": null,
  "reason": null,
  "error": {
    "code": "VERIFIED_RECOMMENDATION_INPUTS_NOT_READY",
    "message": "검증된 운항 정보와 행사 후보를 확인할 수 없어 코스를 생성하지 못했습니다.",
    "retryable": false
  }
}
```

Error messages are user-facing and sanitized. Never return prompts, raw exception text, credentials, tokens, or personal data.

## `POST /api/itineraries/{group_id}/regenerate`

Uses the saved group inputs and the active visitor cookie. The service enforces a maximum of two user regenerations in a database transaction. A regeneration creates a new `run_id` and uses the next index. A no-result regeneration does not consume the user's two successful regeneration slots.

On accepted generation, return `202 Accepted` with `group_id`, the new `run_id`, its `generation_index`, and `status: RUNNING`.

When the limit is exhausted, return `409 Conflict`:

```json
{
  "error": {
    "code": "REGENERATION_LIMIT_REACHED",
    "message": "다시 추천할 수 있는 횟수를 모두 사용했습니다."
  }
}
```

## Operational log mapping

The public result state and operational execution state are different contracts. `agent_runs.status` remains constrained to `RUNNING`, `SUCCESS`, and `FAILED` by migration `003`. A completed `NO_RESULT` is a normal execution (`agent_runs.status = SUCCESS`) with public outcome `NO_RESULT`; do not write `NO_RESULT` into the existing operational log status column.

## Database direction

Use the latest PRD names `visitor_sessions`, `itinerary_groups`, and `itinerary_versions`; do not apply the older `anonymous_sessions`/`recommendation_requests` draft. Store the execution association in `itinerary_versions.execution_id` and keep a durable execution/queue record so status polling also works for failures and no-result outcomes. Keep migration `003_operations_logging.sql` unchanged.

The itinerary item and source enums above are public response contracts; new event/route data may enter a recommendation only after its validation rules and sources are available. The API must not fabricate a course when required verified data is absent.

The current checked-in timetable marks operating days as unspecified and trip linkage as unestablished. Until a verified trip selector and verified event-candidate selector are connected, the worker safely finishes accepted runs as `FAILED` with `VERIFIED_RECOMMENDATION_INPUTS_NOT_READY`; it does not call the LLM or synthesize a course.
