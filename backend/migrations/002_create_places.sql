-- PostgreSQL catalog for the reviewed museum, gallery, park, botanical, and
-- cultural-space master data supplied in database/seoul_places.sqlite3.
-- The source SQLite IDs are stable text identifiers and are preserved as-is.
BEGIN;

CREATE TABLE places (
    place_id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN (
        'MUSEUM', 'ART_GALLERY', 'BOTANICAL_GARDEN', 'PARK',
        'EXHIBITION_HALL', 'CULTURAL_SPACE'
    )),
    name TEXT NOT NULL,
    road_address TEXT,
    lot_address TEXT,
    district TEXT,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(10, 6),
    phone TEXT,
    website TEXT,
    managing_org TEXT,
    description TEXT,
    facilities TEXT,
    opening_hours TEXT,
    closed_days TEXT,
    admission_info TEXT,
    subtype TEXT,
    source_count INTEGER NOT NULL CHECK (source_count > 0),
    primary_source_id TEXT NOT NULL,
    source_url TEXT,
    last_collected_at TIMESTAMPTZ NOT NULL,
    last_verified_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE INDEX places_category_name_idx
    ON places (category, name, place_id)
    WHERE is_active = TRUE;
CREATE INDEX places_district_category_idx
    ON places (district, category)
    WHERE is_active = TRUE;

CREATE TABLE source_records (
    source_record_id TEXT PRIMARY KEY,
    place_id TEXT NOT NULL REFERENCES places (place_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_native_id TEXT,
    raw_name TEXT NOT NULL,
    raw_address TEXT,
    source_reference_date TEXT,
    source_url TEXT NOT NULL
);

CREATE INDEX source_records_place_idx ON source_records (place_id);
CREATE INDEX source_records_source_idx ON source_records (source_id, source_native_id);

CREATE TABLE place_zones (
    zone_id TEXT PRIMARY KEY,
    place_id TEXT NOT NULL REFERENCES places (place_id) ON DELETE CASCADE,
    zone_name TEXT NOT NULL,
    zone_type TEXT NOT NULL,
    open_rule TEXT,
    hours_regular TEXT,
    hours_winter TEXT,
    last_entry TEXT,
    closed_days TEXT,
    adult_fee_krw INTEGER,
    youth_fee_krw INTEGER,
    child_fee_krw INTEGER,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    UNIQUE (place_id, zone_name)
);

CREATE INDEX place_zones_place_idx ON place_zones (place_id, zone_name);

COMMENT ON TABLE places IS
    'Integrated Seoul place catalog; source records and verified sub-zones are stored separately.';
COMMENT ON COLUMN places.category IS
    'Mapped from source place_type. CULTURAL_SPACE is preserved separately and is not an exhibition-hall assertion.';
COMMENT ON COLUMN places.last_verified_at IS
    'Manual operational verification timestamp. It is not inferred from last_collected_at.';
COMMENT ON COLUMN places.opening_hours IS
    'Original normalized source text; detailed seasonal rules may be in place_zones.';

COMMIT;
