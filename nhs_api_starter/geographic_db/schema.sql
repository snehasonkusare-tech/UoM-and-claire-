-- Geographic area warehouse schema.
-- Matches the ER diagram: GEOGRAPHIC_AREA is self-referencing via
-- parent_geographic_area_id (optional; null for root areas, e.g. boroughs).
--
-- Run this whole file in MySQL Workbench before running
-- ingest_geographic_to_mysql.py. Lives in its own geographic_areas database
-- on the same server as nhs_directory (db/schema.sql) and nhs_symptoms
-- (symptoms_db/schema.sql) -- no shared tables with either.
--
-- geographic_area_id is declared CHAR(36), not a native UUID type: MySQL 8.0
-- has no UUID column type (UUID() is just a function returning a string), so
-- CHAR(36) storing the canonical 8-4-4-4-12 hex string is the standard
-- portable stand-in.

CREATE DATABASE IF NOT EXISTS geographic_areas
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE geographic_areas;

-- geographic_area_id is deterministic (uuid5, hashed from area_name +
-- area_type + official_area_code), so re-running the ingest against the same
-- source spreadsheet updates rows in place instead of duplicating them --
-- same idempotent upsert pattern as symptoms_db/ingest_symptoms_to_mysql.py.
CREATE TABLE IF NOT EXISTS geographic_area (
    geographic_area_id         CHAR(36)     NOT NULL PRIMARY KEY,
    area_name                  VARCHAR(255) NOT NULL,
    area_type                  VARCHAR(100) NOT NULL COMMENT 'region, borough, ward, neighbourhood, postcode, etc.',
    official_area_code         VARCHAR(50)  NULL,
    created_at                 DATETIME     NOT NULL,
    updated_at                 DATETIME     NOT NULL,
    parent_geographic_area_id  CHAR(36)     NULL COMMENT 'optional; null for root areas',
    UNIQUE KEY uq_geographic_area_natural (area_name, area_type, official_area_code),
    KEY ix_geographic_area_parent (parent_geographic_area_id),
    CONSTRAINT fk_geographic_area_parent
        FOREIGN KEY (parent_geographic_area_id)
        REFERENCES geographic_area (geographic_area_id)
        ON DELETE SET NULL
) ENGINE=InnoDB;
