import type { ItineraryItem, Sailing } from "../api/types";
import { KakaoRouteMap } from "../components/KakaoRouteMap";
import {
  CalendarIcon,
  ShareIcon,
  ShipIcon,
  SparkIcon,
  WarningIcon,
} from "../components/icons";
import { StepIndicator } from "../components/StepIndicator";
import { formatTime } from "../recommendation";
import type { PlannerStep } from "../plannerFlow";
import type { ViewState } from "./types";

interface RecommendationPageProps {
  view: ViewState;
  selected: Sailing | undefined;
  loading: boolean;
  originName: string;
  destinationName: string;
  onRetry: () => void;
  onRegenerate: () => void;
  onShare: () => void;
  onPrint: () => void;
  onSetStep: (step: PlannerStep) => void;
}

export function RecommendationPage({
  view,
  selected,
  loading,
  originName,
  destinationName,
  onRetry,
  onRegenerate,
  onShare,
  onPrint,
  onSetStep,
}: RecommendationPageProps) {
  const sailing = view.phase === "idle" ? selected : view.sailing;
  const announcement =
    view.phase === "loading"
      ? "선택한 조건으로 추천 코스를 만들고 있습니다."
      : view.phase === "error"
        ? "추천 요청을 처리하지 못했습니다."
        : view.phase === "done" && view.run.status === "SUCCESS"
          ? "추천 코스가 준비되었습니다."
          : view.phase === "done" && view.run.status === "NO_RESULT"
            ? "조건에 맞는 코스를 찾지 못했습니다."
            : view.phase === "done" && view.run.status === "FAILED"
              ? "추천을 완료하지 못했습니다."
              : "추천 결과가 없습니다.";

  return (
    <section className="wizard-screen" aria-label="한강버스 나들이 플래너">
      <StepIndicator step="result" />
      <section
        className="wizard-panel"
        aria-labelledby="results-heading"
        aria-busy={loading}
      >
        <div className="wizard-heading result-step-heading">
          <div className="heading-icon"><SparkIcon /></div>
          <div>
            <p className="eyebrow">STEP 3 · YOUR RECOMMENDATION</p>
            <h1 id="results-heading">AI 추천 나들이 코스</h1>
            <p>
              {originName} 출발 · {destinationName} 도착 예정 ·{" "}
              {sailing ? formatTime(sailing.departure) : "선택한 배편"}
            </p>
          </div>
        </div>
        <div className="results-panel">
          <div className="result-announcement" role="status" aria-live="polite">
            {announcement}
          </div>
          {view.phase === "loading" && (
            <div className="recommendation-wait" role="status">
              <span className="spinner" aria-hidden="true" />
              <div>
                <h2>나들이 코스를 준비하고 있어요</h2>
                <p>운항과 행사 정보를 확인하고 일정을 구성하고 있습니다.</p>
              </div>
            </div>
          )}
          {view.phase === "error" && (
            <div className="service-alert" role="alert">
              <div className="alert-icon"><WarningIcon /></div>
              <div className="alert-copy">
                <p className="eyebrow">추천 요청 오류</p>
                <h2>추천 결과를 확인하지 못했습니다</h2>
                <p>{view.message}</p>
              </div>
              {view.retryable && (
                <button type="button" className="outline-button" onClick={onRetry}>
                  다시 확인
                </button>
              )}
            </div>
          )}
          {view.phase === "done" && (
            <ItineraryOutcome
              view={view}
              onRegenerate={onRegenerate}
              onShare={onShare}
              onPrint={onPrint}
            />
          )}
          {view.phase === "idle" && (
            <p className="status-note" role="status">
              추천을 시작하려면 조건 화면에서 코스 추천을 요청해 주세요.
            </p>
          )}
          <div className="results-note">
            <span className="info-mark">i</span>
            <p>
              시간표의 도착 시각은 실제 운항 상황과 다를 수 있습니다. 행사 운영
              여부와 입장 조건은 방문 전 공식 안내를 확인해 주세요.
            </p>
          </div>
        </div>
        <div className="wizard-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => onSetStep("route")}
          >
            선착장 변경
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={() => onSetStep("preferences")}
          >
            조건 수정
          </button>
        </div>
      </section>
    </section>
  );
}

function ItineraryOutcome({
  view,
  onRegenerate,
  onShare,
  onPrint,
}: {
  view: Extract<ViewState, { phase: "done" }>;
  onRegenerate: () => void;
  onShare: () => void;
  onPrint: () => void;
}) {
  const { run } = view;
  const canRegenerate = run.generation_index < 2;

  if (run.status === "NO_RESULT") {
    return (
      <div className="itinerary-state itinerary-no-result" role="status">
        <span className="state-symbol"><CalendarIcon /></span>
        <p className="eyebrow">추천 결과 없음</p>
        <h2>조건에 맞는 코스를 찾지 못했습니다</h2>
        <p>
          {run.reason?.message ??
            "검증된 운항 정보와 문화행사 후보를 찾지 못했습니다."}
        </p>
        {canRegenerate && (
          <button className="primary-button" type="button" onClick={onRegenerate}>
            다른 코스 생성
          </button>
        )}
      </div>
    );
  }

  if (run.status === "FAILED" || !run.itinerary) {
    const retryable =
      run.status === "FAILED" && Boolean(run.error?.retryable) && canRegenerate;
    return (
      <div className="service-alert" role="alert">
        <div className="alert-icon"><WarningIcon /></div>
        <div className="alert-copy">
          <p className="eyebrow">추천 기능 이용 불가</p>
          <h2>코스를 생성하지 못했습니다</h2>
          <p>
            {run.error?.message ??
              "추천 결과를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."}
          </p>
        </div>
        {retryable && (
          <button type="button" className="outline-button" onClick={onRegenerate}>
            다시 추천
          </button>
        )}
      </div>
    );
  }

  const { itinerary } = run;
  return (
    <>
      <div className="itinerary-result-heading">
        <div>
          <p className="eyebrow">추천 코스 · {run.generation_index + 1}/3</p>
          <h2>{itinerary.title}</h2>
        </div>
        {canRegenerate && (
          <button className="outline-button" type="button" onClick={onRegenerate}>
            다른 코스 생성
          </button>
        )}
      </div>
      <ol className="itinerary-list" aria-label="추천 일정">
        {itinerary.items.map((item) => (
          <ItineraryItemCard key={item.sequence_no} item={item} />
        ))}
      </ol>
      <KakaoRouteMap items={itinerary.items} sailing={view.sailing} />
      <div className="result-actions">
        <button className="outline-button" type="button" onClick={onShare}>
          <ShareIcon /> 조건 공유
        </button>
        <button
          className="primary-button print-button"
          type="button"
          onClick={onPrint}
        >
          <span aria-hidden="true">↓</span> PDF 저장 / 인쇄
        </button>
      </div>
    </>
  );
}

function ItineraryItemCard({ item }: { item: ItineraryItem }) {
  const typeLabel = {
    FERRY: "한강버스",
    EVENT: "문화행사",
    PLACE: "장소",
    WALK: "산책",
  }[item.item_type];
  const stopRoute =
    item.departure_stop && item.arrival_stop
      ? item.departure_stop.name + " → " + item.arrival_stop.name
      : null;
  const placeLine = [item.place_name, item.address].filter(Boolean).join(" · ");

  return (
    <li className="itinerary-item">
      <div className="itinerary-item-marker" aria-hidden="true">
        {item.item_type === "FERRY" ? <ShipIcon /> : <CalendarIcon />}
      </div>
      <article className="itinerary-item-card">
        <div className="itinerary-item-meta">
          <span className="itinerary-type">{typeLabel}</span>
          <span>{formatItemTime(item.start_at, item.end_at)}</span>
        </div>
        <h3>{item.title}</h3>
        {(stopRoute || placeLine) && (
          <p className="itinerary-location">{stopRoute ?? placeLine}</p>
        )}
        <p className="itinerary-reason">{item.reason}</p>
        {item.sources.length > 0 && (
          <ul className="itinerary-sources" aria-label="추천 근거">
            {item.sources.map((source, index) => (
              <li key={source.type + "-" + source.title + "-" + index}>
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer">
                    {source.title} ↗
                  </a>
                ) : (
                  <span>{source.title}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </article>
    </li>
  );
}

function formatItemTime(startAt: string | null, endAt: string | null): string {
  const format = (value: string) =>
    new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value));
  if (!startAt) return "시간 미정";
  return endAt ? format(startAt) + "–" + format(endAt) : format(startAt);
}
