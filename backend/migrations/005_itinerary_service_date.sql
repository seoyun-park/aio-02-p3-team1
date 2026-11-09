-- Store the user-selected local service date for itinerary generation.
BEGIN;

ALTER TABLE itinerary_groups
    ADD COLUMN service_date DATE NOT NULL DEFAULT CURRENT_DATE;

COMMENT ON COLUMN itinerary_groups.service_date IS
    'User-selected service date in Asia/Seoul; timetable departure estimates are reused for every date because the source does not specify weekdays.';

COMMIT;
