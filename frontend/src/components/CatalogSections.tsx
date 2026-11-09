import { useEffect, useState } from "react";
import {
  CatalogApiError,
  catalogClient,
  type CultureEventListResponse,
  type PlaceCategory,
  type PlaceListResponse,
} from "../api/catalogs";
import { filterEventsByCategory, listEventCategories } from "../catalogFiltering";

const PAGE_SIZE = 8;

type ResourceState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

type PageLoader<T, F extends string | null> = (
  limit: number,
  offset: number,
  signal: AbortSignal,
  filter: F,
) => Promise<T>;

function useCatalogPage<T, F extends string | null>(
  loader: PageLoader<T, F>,
  kind: "events" | "places",
  totalCountOf: (data: T) => number,
  itemCountOf: (data: T) => number,
  initialFilter: F,
) {
  const [offset, setOffset] = useState(0);
  const [retryCount, setRetryCount] = useState(0);
  const [filter, setFilter] = useState<F>(initialFilter);
  const [resource, setResource] = useState<ResourceState<T>>({
    status: "loading",
  });

  useEffect(() => {
    const controller = new AbortController();
    loader(PAGE_SIZE, offset, controller.signal, filter)
      .then((data) => setResource({ status: "ready", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof CatalogApiError &&
          (error.status === 401 || error.status === 403)
            ? "로그인 상태를 확인한 뒤 다시 시도해 주세요."
            : kind === "events"
              ? "서울시 문화행사 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
              : "서울시 장소 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
        setResource({ status: "error", message });
      });

    return () => controller.abort();
  }, [filter, kind, loader, offset, retryCount]);

  const totalCount =
    resource.status === "ready" ? totalCountOf(resource.data) : 0;
  const itemCount =
    resource.status === "ready" ? itemCountOf(resource.data) : 0;

  return {
    resource,
    filter,
    offset,
    totalCount,
    itemCount,
    pageNumber: Math.floor(offset / PAGE_SIZE) + 1,
    pageCount: Math.max(1, Math.ceil(totalCount / PAGE_SIZE)),
    hasPrevious: offset > 0,
    hasNext: offset + itemCount < totalCount,
    selectFilter: (nextFilter: F) => {
      if (filter === nextFilter && offset === 0) return;
      setResource({ status: "loading" });
      setOffset(0);
      setFilter(nextFilter);
    },
    previous: () => {
      setResource({ status: "loading" });
      setOffset((current) => Math.max(0, current - PAGE_SIZE));
    },
    next: () => {
      setResource({ status: "loading" });
      setOffset((current) => current + PAGE_SIZE);
    },
    retry: () => {
      setResource({ status: "loading" });
      setRetryCount((current) => current + 1);
    },
  };
}

const loadEvents: PageLoader<CultureEventListResponse, string | null> = (
  limit,
  offset,
  signal,
) => catalogClient.getEvents(limit, offset, signal);
const loadPlaces: PageLoader<PlaceListResponse, PlaceCategory | null> = (
  limit,
  offset,
  signal,
  category,
) => catalogClient.getPlaces(limit, offset, signal, category);
const eventTotalCount = (response: CultureEventListResponse) =>
  response.total_count;
const eventItemCount = (response: CultureEventListResponse) =>
  response.events.length;
const placeTotalCount = (response: PlaceListResponse) => response.total_count;
const placeItemCount = (response: PlaceListResponse) => response.places.length;

const placeCategoryNames: Record<PlaceCategory, string> = {
  MUSEUM: "박물관",
  ART_GALLERY: "미술관",
  BOTANICAL_GARDEN: "식물원",
  PARK: "공원",
  EXHIBITION_HALL: "전시장",
  CULTURAL_SPACE: "문화공간",
};

const placeCategoryOptions: Array<{ value: PlaceCategory; label: string }> = [
  { value: "MUSEUM", label: placeCategoryNames.MUSEUM },
  { value: "ART_GALLERY", label: placeCategoryNames.ART_GALLERY },
  { value: "BOTANICAL_GARDEN", label: placeCategoryNames.BOTANICAL_GARDEN },
  { value: "PARK", label: placeCategoryNames.PARK },
  { value: "EXHIBITION_HALL", label: placeCategoryNames.EXHIBITION_HALL },
  { value: "CULTURAL_SPACE", label: placeCategoryNames.CULTURAL_SPACE },
];

function CategoryFilters<T extends string>({
  label,
  selected,
  options,
  totalCount,
  onSelect,
}: {
  label: string;
  selected: T | null;
  options: Array<{ value: T; label: string; count?: number }>;
  totalCount?: number;
  onSelect: (value: T | null) => void;
}) {
  return (
    <div className="catalog-category-filter" role="group" aria-label={label}>
      <button
        className={selected === null ? "is-active" : ""}
        type="button"
        aria-pressed={selected === null}
        onClick={() => onSelect(null)}
      >
        전체
        {totalCount !== undefined && <span>{totalCount}</span>}
      </button>
      {options.map((option) => (
        <button
          className={selected === option.value ? "is-active" : ""}
          type="button"
          aria-pressed={selected === option.value}
          key={option.value}
          onClick={() => onSelect(option.value)}
        >
          {option.label}
          {option.count !== undefined && <span>{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

function eventPeriod(event: CultureEventListResponse["events"][number]) {
  const { start_date_text: start, end_date_text: end, event_date_text: date } =
    event;
  if (start && end) return start === end ? start : `${start} – ${end}`;
  return start ?? end ?? date ?? "일정 확인 필요";
}

function eventFee(event: CultureEventListResponse["events"][number]) {
  if (event.free_status === "free") return "무료";
  if (event.free_status === "paid_with_details")
    return event.fee_text ? `유료 · ${event.fee_text}` : "유료";
  if (event.free_status === "paid_details_missing") return "유료 · 금액 미확인";
  return event.fee_text ?? "요금 정보 미확인";
}

function CatalogPagination({
  pageNumber,
  pageCount,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
}: {
  pageNumber: number;
  pageCount: number;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <nav className="catalog-pagination" aria-label="목록 페이지 이동">
      <button
        className="outline-button"
        type="button"
        onClick={onPrevious}
        disabled={!hasPrevious}
      >
        이전 목록
      </button>
      <span>
        {pageNumber} / {pageCount}
      </span>
      <button
        className="outline-button"
        type="button"
        onClick={onNext}
        disabled={!hasNext}
      >
        다음 목록
      </button>
    </nav>
  );
}

export function CatalogSections() {
  const events = useCatalogPage<CultureEventListResponse, string | null>(
    loadEvents,
    "events",
    eventTotalCount,
    eventItemCount,
    null,
  );
  const places = useCatalogPage<PlaceListResponse, PlaceCategory | null>(
    loadPlaces,
    "places",
    placeTotalCount,
    placeItemCount,
    null,
  );

  const loadedEvents =
    events.resource.status === "ready" ? events.resource.data.events : [];
  const eventCategoryOptions = listEventCategories(loadedEvents).map(
    (category) => ({
      value: category,
      label: category,
      count: loadedEvents.filter((event) => event.category?.trim() === category)
        .length,
    }),
  );
  if (
    events.filter &&
    !eventCategoryOptions.some((option) => option.value === events.filter)
  ) {
    eventCategoryOptions.push({ value: events.filter, label: events.filter, count: 0 });
  }
  const visibleEvents = filterEventsByCategory(loadedEvents, events.filter);

  return (
    <div className="catalog-sections">
      <section
        className="catalog-section"
        aria-labelledby="culture-catalog-heading"
        aria-busy={events.resource.status === "loading"}
      >
        <header className="catalog-section-heading">
          <div>
            <p className="eyebrow">SEOUL CULTURE · OFFICIAL LIST</p>
            <h2 id="culture-catalog-heading">서울시 문화행사</h2>
            <p>
              서울시 문화행사 정보를 확인합니다. 행사 운영·예약 여부는 공식 안내를
              확인해 주세요.
            </p>
          </div>
          {events.resource.status === "ready" && (
            <span className="catalog-total">
              전체 {events.resource.data.total_count.toLocaleString()}건
            </span>
          )}
        </header>

        {events.resource.status === "loading" && (
          <p className="catalog-status" role="status">
            문화행사 정보를 불러오고 있습니다.
          </p>
        )}
        {events.resource.status === "error" && (
          <div className="catalog-error" role="alert">
            <p>{events.resource.message}</p>
            <button className="outline-button" type="button" onClick={events.retry}>
              다시 시도
            </button>
          </div>
        )}
        {events.resource.status === "ready" && (
          <>
            <CategoryFilters
              label="문화행사 분류"
              selected={events.filter}
              options={eventCategoryOptions}
              totalCount={events.resource.data.events.length}
              onSelect={events.selectFilter}
            />
            <p className="catalog-filter-note">
              행사 분류는 현재 불러온 목록에서 선택할 수 있어요.
            </p>
            {events.resource.data.events.length === 0 ? (
              <p className="catalog-status" role="status">
                현재 표시할 문화행사가 없습니다.
              </p>
            ) : visibleEvents.length === 0 ? (
              <p className="catalog-status" role="status">
                현재 목록에는 선택한 분류의 행사가 없습니다. 다른 목록을 확인하거나 전체를 선택해 주세요.
              </p>
            ) : (
              <div className="catalog-event-grid">
                {visibleEvents.map((event, index) => {
                  const imageUrl = safeExternalUrl(event.main_image_url);
                  const detailUrl =
                    safeExternalUrl(event.culture_detail_url) ??
                    safeExternalUrl(event.organization_url);
                  return (
                    <article
                      className="catalog-event-card"
                      key={`${event.title ?? "event"}-${event.place ?? "place"}-${index}`}
                    >
                      <div className="catalog-event-image">
                        {imageUrl && (
                          <img
                            src={imageUrl}
                            alt=""
                            loading="lazy"
                            onError={(error) => {
                              error.currentTarget.hidden = true;
                            }}
                          />
                        )}
                        <span>{event.category ?? "문화행사"}</span>
                      </div>
                      <div className="catalog-event-copy">
                        <p className="catalog-event-meta">
                          {event.district_name ?? "서울"} · {eventPeriod(event)}
                        </p>
                        <h3>{event.title ?? "행사명 미제공"}</h3>
                        <p className="catalog-event-place">
                          {event.place ?? "장소 확인 필요"}
                        </p>
                        <p className="catalog-event-fee">{eventFee(event)}</p>
                        {detailUrl && (
                          <a
                            className="catalog-source-link"
                            href={detailUrl}
                            target="_blank"
                            rel="noreferrer"
                          >
                            행사 상세 보기 <span aria-hidden="true">↗</span>
                          </a>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
            <CatalogPagination
              pageNumber={events.pageNumber}
              pageCount={events.pageCount}
              hasPrevious={events.hasPrevious}
              hasNext={events.hasNext}
              onPrevious={events.previous}
              onNext={events.next}
            />
          </>
        )}
      </section>

      <section
        className="catalog-section"
        aria-labelledby="place-catalog-heading"
        aria-busy={places.resource.status === "loading"}
      >
        <header className="catalog-section-heading">
          <div>
            <p className="eyebrow">SEOUL PLACES · VERIFIED CATALOG</p>
            <h2 id="place-catalog-heading">서울 문화·휴식 공간</h2>
            <p>
              박물관·공원·문화시설 등 서울의 장소 정보입니다. 운영 시간과 휴관일은
              방문 전에 확인해 주세요.
            </p>
          </div>
          {places.resource.status === "ready" && (
            <span className="catalog-total">
              {places.filter
                ? `${placeCategoryNames[places.filter]} ${places.resource.data.total_count.toLocaleString()}곳`
                : `전체 ${places.resource.data.total_count.toLocaleString()}곳`}
            </span>
          )}
        </header>

        {places.resource.status === "loading" && (
          <p className="catalog-status" role="status">
            장소 정보를 불러오고 있습니다.
          </p>
        )}
        {places.resource.status === "error" && (
          <div className="catalog-error" role="alert">
            <p>{places.resource.message}</p>
            <button className="outline-button" type="button" onClick={places.retry}>
              다시 시도
            </button>
          </div>
        )}
        {places.resource.status === "ready" && (
          <>
            <CategoryFilters
              label="문화·휴식 공간 분류"
              selected={places.filter}
              options={placeCategoryOptions}
              onSelect={places.selectFilter}
            />
            {places.resource.data.places.length === 0 ? (
              <p className="catalog-status" role="status">
                {places.filter
                  ? `현재 표시할 ${placeCategoryNames[places.filter]}이(가) 없습니다.`
                  : "현재 표시할 장소가 없습니다."}
              </p>
            ) : (
              <div className="catalog-place-grid">
                {places.resource.data.places.map((place) => {
                  const detailUrl =
                    safeExternalUrl(place.website) ??
                    safeExternalUrl(place.source_url);
                  return (
                    <article className="catalog-place-card" key={place.place_id}>
                      <p className="catalog-place-category">
                        {placeCategoryNames[place.category]}
                        {place.district && <span>서울 {place.district}</span>}
                      </p>
                      <h3>{place.name}</h3>
                      <p className="catalog-place-address">
                        {place.road_address ?? place.lot_address ?? "주소 확인 필요"}
                      </p>
                      <dl className="catalog-place-details">
                        {place.opening_hours && (
                          <div>
                            <dt>운영</dt>
                            <dd>{place.opening_hours}</dd>
                          </div>
                        )}
                        {place.closed_days && (
                          <div>
                            <dt>휴관</dt>
                            <dd>{place.closed_days}</dd>
                          </div>
                        )}
                        {place.admission_info && (
                          <div>
                            <dt>이용</dt>
                            <dd>{place.admission_info}</dd>
                          </div>
                        )}
                      </dl>
                      {detailUrl && (
                        <a
                          className="catalog-source-link"
                          href={detailUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          장소 정보 보기 <span aria-hidden="true">↗</span>
                        </a>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
            <CatalogPagination
              pageNumber={places.pageNumber}
              pageCount={places.pageCount}
              hasPrevious={places.hasPrevious}
              hasNext={places.hasNext}
              onPrevious={places.previous}
              onNext={places.next}
            />
          </>
        )}
      </section>
    </div>
  );
}
