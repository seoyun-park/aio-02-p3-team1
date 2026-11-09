import type { Exhibition, Sailing } from "../api/types";
import { formatTime } from "../recommendation";

export function EventCard({
  event,
  sailing,
  limited,
}: {
  event: Exhibition;
  sailing: Sailing;
  limited: boolean;
}) {
  const endsAt = event.totalMinutes === null
    ? null
    : new Date(
      Date.parse(sailing.departure) + event.totalMinutes * 60_000,
    ).toISOString();
  const movementLabel = event.travelMinutes === null
    ? "이동 시간 미확인"
    : `하선 후 이동 ${event.travelMinutes}분`;
  const durationLabel = event.travelMinutes === null || event.visitMinutes === null
    ? "이동·관람 시간 미확인"
    : `이동 ${event.travelMinutes}분 · 관람 ${event.visitMinutes}분`;
  const scheduleLabel = event.startAt
    ? `${formatTime(event.startAt)} 시작 예정`
    : "행사 시간 미확인";

  return (
    <article className="event-card">
      <div className={`exhibition-art ${event.id}`} aria-hidden="true">
        <span className="art-kicker">{event.category}</span>
        <div className="art-shape" />
        <span className="art-skyline" />
      </div>
      <div className="event-body">
        <p className="eyebrow">{event.category}</p>
        <h3>{event.title}</h3>
        <p className="event-place">
          {event.place} <span>· {movementLabel}</span>
        </p>
        <p className="event-reason">
          {limited
            ? "추천 설명을 제공할 수 없어 기본 정보만 안내합니다."
            : event.reason}
        </p>
        <dl className="event-details">
          <div>
            <dt>이동·관람</dt>
            <dd>{durationLabel}</dd>
          </div>
          <div>
            <dt>운영·요금</dt>
            <dd>
              {event.admission} · {event.fee}
            </dd>
          </div>
          <div>
            <dt>일정 예시</dt>
            <dd>
              {scheduleLabel}
              {endsAt && (
                <span className="screen-reader-only">
                  {" "}종료 예상 {formatTime(endsAt)}
                </span>
              )}
            </dd>
          </div>
        </dl>
        <footer className="event-source">
          {event.collectedAt && event.reviewedAt
            ? `수집 ${formatTime(event.collectedAt)} · 검수 ${formatTime(event.reviewedAt)} KST`
            : "백엔드 검수 결과 기반 추천"}
          {event.sourceUrl && (
            <a href={event.sourceUrl} target="_blank" rel="noreferrer"> 출처</a>
          )}
        </footer>
      </div>
    </article>
  );
}
