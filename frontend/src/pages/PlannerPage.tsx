import type { Dispatch, FormEventHandler, SetStateAction } from "react";
import type {
  ActivityType,
  CompanionType,
  Sailing,
  Stop,
  TimetableMetadata,
  TripDeparture,
} from "../api/types";
import { ApiNoticeCard } from "../components/ApiNoticeCard";
import { CalendarIcon, ShipIcon, SparkIcon, WarningIcon } from "../components/icons";
import { StationMap } from "../components/StationMap";
import { StepIndicator } from "../components/StepIndicator";
import type { PierSelectionTarget } from "../stationMap";
import { formatTime } from "../recommendation";
import type { PlannerStep } from "../plannerFlow";
import type { Notice, RequestPhase } from "./types";

interface PlannerPageProps {
  step: Exclude<PlannerStep, "home" | "result">;
  stops: Stop[];
  stopsPhase: RequestPhase;
  stopsNotice: Notice | null;
  retryStops: () => void;
  fromStopId: string;
  toStopId: string;
  activeTarget: PierSelectionTarget;
  loading: boolean;
  setActiveTarget: Dispatch<SetStateAction<PierSelectionTarget>>;
  selectMapStop: (stopId: string) => void;
  formError: string;
  tripsNotice: Notice | null;
  retryTrips: () => void;
  tripsPhase: RequestPhase;
  trips: TripDeparture[];
  tripMetadata: TimetableMetadata | null;
  serviceDate: string;
  minServiceDate: string;
  onServiceDateChange: (serviceDate: string) => void;
  setStep: Dispatch<SetStateAction<PlannerStep>>;
  continueFromRoute: () => void;
  originName: string;
  destinationName: string;
  sailings: Sailing[];
  selected: Sailing | undefined;
  sailingId: string;
  resetResult: () => void;
  setSailingId: Dispatch<SetStateAction<string>>;
  companionType: CompanionType;
  setCompanionType: Dispatch<SetStateAction<CompanionType>>;
  activityType: ActivityType;
  setActivityType: Dispatch<SetStateAction<ActivityType>>;
  submit: FormEventHandler<HTMLFormElement>;
}

export function PlannerPage({
  step,
  stops,
  stopsPhase,
  stopsNotice,
  retryStops,
  fromStopId,
  toStopId,
  activeTarget,
  loading,
  setActiveTarget,
  selectMapStop,
  formError,
  tripsNotice,
  retryTrips,
  tripsPhase,
  trips,
  tripMetadata,
  serviceDate,
  minServiceDate,
  onServiceDateChange,
  setStep,
  continueFromRoute,
  originName,
  destinationName,
  sailings,
  selected,
  sailingId,
  resetResult,
  setSailingId,
  companionType,
  setCompanionType,
  activityType,
  setActivityType,
  submit,
}: PlannerPageProps) {
  return (
    <section className="wizard-screen" aria-label="한강버스 나들이 플래너">
      <StepIndicator step={step} />
            {step === "route" && (
              <section className="wizard-panel" aria-labelledby="route-heading">
                <div className="wizard-heading">
                  <div className="heading-icon"><ShipIcon /></div>
                  <div>
                    <p className="eyebrow">STEP 1 · YOUR ROUTE</p>
                    <h1 id="route-heading">한강 지도에서 여정을 선택해 주세요</h1>
                    <p>출발 선착장과 도착 선착장을 지도에서 차례로 선택합니다.</p>
                  </div>
                </div>

                <StationMap
                  stops={stops}
                  stopsPhase={stopsPhase}
                  fromStopId={fromStopId}
                  toStopId={toStopId}
                  activeTarget={activeTarget}
                  disabled={loading}
                  onTargetChange={setActiveTarget}
                  onSelectStop={selectMapStop}
                />

                {formError && (
                  <p className="form-error" role="alert"><WarningIcon />{formError}</p>
                )}
                {stopsPhase === "error" && stopsNotice && (
                  <ApiNoticeCard notice={stopsNotice} onRetry={retryStops} />
                )}
                {tripsPhase === "error" && tripsNotice && (
                  <ApiNoticeCard notice={tripsNotice} onRetry={retryTrips} />
                )}
                {stopsPhase === "ready" && tripsPhase === "ready" && fromStopId && toStopId && trips.length === 0 && (
                  <p className="status-note" role="status">선택한 날짜에는 이 구간의 운항 회차가 없습니다. 다른 구간이나 날짜를 선택해 주세요.</p>
                )}
                {tripsPhase === "ready" && trips.length > 0 && tripMetadata && (
                  <div className="timetable-note">
                    <span className="info-mark">i</span>
                    <p>
                      한강버스는 매일 운항하지만, {tripMetadata.weather_disruption_notice} 도착 시각은 시간표의 소요시간으로 계산한 예상값입니다.
                      <a href={tripMetadata.source_url} target="_blank" rel="noreferrer">시간표 출처 보기</a>
                    </p>
                  </div>
                )}

                <div className="wizard-actions">
                  <button className="secondary-button" type="button" onClick={() => setStep("home")}>처음으로</button>
                  <button className="primary-button" type="button" onClick={continueFromRoute}>배편 선택하기 <span aria-hidden="true">→</span></button>
                </div>
              </section>
            )}
            {step === "preferences" && (
              <section className="wizard-panel" aria-labelledby="preferences-heading">
                <div className="wizard-heading">
                  <div className="heading-icon"><SparkIcon /></div>
                  <div>
                    <p className="eyebrow">STEP 2 · YOUR PREFERENCES</p>
                    <h1 id="preferences-heading">어떤 나들이를 원하시나요?</h1>
                    <p>{originName}에서 출발해 {destinationName}에 도착하는 여정입니다.</p>
                  </div>
                </div>

                <form id="planner-form" className="preferences-form" onSubmit={submit} noValidate>
                  <div className="form-grid preferences-grid">
                    <div className="form-field">
                      <label htmlFor="service-date"><span className="field-symbol"><CalendarIcon /></span> 여행 날짜</label>
                      <input
                        id="service-date"
                        type="date"
                        value={serviceDate}
                        min={minServiceDate}
                        disabled={loading}
                        onChange={(event) => onServiceDateChange(event.target.value)}
                      />
                    </div>
                    <div className="form-field">
                      <label htmlFor="sailing"><span className="field-symbol clock-symbol">◷</span> 출발 회차</label>
                      <select
                        id="sailing"
                        value={selected ? sailingId : ""}
                        disabled={tripsPhase !== "ready" || loading || sailings.length === 0}
                        aria-invalid={Boolean(formError && !selected)}
                        onChange={(event) => {
                          resetResult();
                          setSailingId(event.target.value);
                        }}
                      >
                        <option value="">
                          {tripsPhase === "loading" ? "시간표를 불러오는 중" : sailings.length ? "출발 시각을 선택해 주세요" : "선택한 날짜의 회차가 없습니다"}
                        </option>
                        {sailings.map((sailing) => (
                          <option key={sailing.id} value={sailing.id}>
                            {formatTime(sailing.departure)} 출발 · {formatTime(sailing.arrival)} 도착 예정
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="form-field">
                      <label htmlFor="companion-type"><span className="field-symbol">♧</span> 누구와 함께?</label>
                      <select
                        id="companion-type"
                        value={companionType}
                        onChange={(event) => {
                          resetResult();
                          setCompanionType(event.target.value as CompanionType);
                        }}
                      >
                        <option value="family">가족</option>
                        <option value="couple">연인</option>
                        <option value="friends">친구</option>
                        <option value="solo">혼자</option>
                      </select>
                    </div>

                    <div className="form-field">
                      <label htmlFor="activity-type"><span className="field-symbol">✧</span> 선호하는 활동</label>
                      <select
                        id="activity-type"
                        value={activityType}
                        onChange={(event) => {
                          resetResult();
                          setActivityType(event.target.value as ActivityType);
                        }}
                      >
                        <option value="exhibition_performance">전시 · 공연</option>
                        <option value="culture_event">문화행사</option>
                        <option value="walk_healing">산책 · 휴식</option>
                      </select>
                    </div>
                  </div>

                  {tripsPhase === "ready" && sailings.length === 0 && (
                    <div className="step-alert step-alert-info" role="status">
                      <span className="alert-icon"><CalendarIcon /></span>
                      <div><strong>선택한 날짜에 배편이 없습니다</strong><p>다른 날짜나 선착장 구간을 선택해 주세요.</p></div>
                    </div>
                  )}
                  {formError && (
                    <p id="planner-error" className="form-error" role="alert"><WarningIcon />{formError}</p>
                  )}
                  {tripMetadata && trips.length > 0 && (
                    <div className="timetable-note">
                      <span className="info-mark">i</span>
                      <p>한강버스는 매일 운항하지만, {tripMetadata.weather_disruption_notice} 도착 시각은 시간표의 소요시간으로 계산한 예상값입니다. <a href={tripMetadata.source_url} target="_blank" rel="noreferrer">시간표 출처 보기</a></p>
                    </div>
                  )}

                  <div className="wizard-actions">
                    <button className="secondary-button" type="button" onClick={() => setStep("route")}>이전</button>
                    <button className="primary-button" type="submit" disabled={loading || tripsPhase === "loading"}>
                      <SparkIcon /> {loading ? "추천 결과를 준비하고 있어요" : "AI 코스 추천하기"} <span aria-hidden="true">→</span>
                    </button>
                  </div>
                </form>
              </section>
            )}
    </section>
  );
}
