import type { Sailing, TripDeparture } from "./api/types";

export function todayInSeoul(now = new Date()): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function sailingFromTrip(
  trip: TripDeparture,
  serviceDate: string,
): Sailing {
  const toKstInstant = (time: string, dayOffset = 0): string => {
    if (!/^\d{2}:\d{2}$/.test(time))
      throw new Error("시간표 시각은 HH:mm 형식이어야 합니다.");
    const instant = Date.parse(`${serviceDate}T${time}:00+09:00`);
    if (!Number.isFinite(instant))
      throw new Error("시간표 날짜를 확인할 수 없습니다.");
    return new Date(instant + dayOffset * 24 * 60 * 60 * 1000).toISOString();
  };

  return {
    id: `${trip.from_stop_id}-${trip.to_stop_id}-${trip.departure_time}`,
    departure: toKstInstant(trip.departure_time),
    arrival: toKstInstant(trip.estimated_arrival_time, trip.arrival_day_offset),
    originStopId: trip.from_stop_id,
    destinationStopId: trip.to_stop_id,
    travelMinutes: trip.travel_minutes,
    stopCallStatus: trip.stop_call_status,
  };
}

export function availableSailings<T extends { departure: string }>(
  items: T[],
  now: Date,
): T[] {
  return items.filter(
    (item) =>
      Number.isFinite(Date.parse(item.departure)) &&
      Date.parse(item.departure) > now.getTime(),
  );
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}
