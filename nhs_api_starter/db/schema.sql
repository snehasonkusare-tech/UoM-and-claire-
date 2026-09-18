-- Directory of Healthcare Services (DoHS) v3 warehouse schema.
-- Matches the ER diagram: ORGANISATION_CLASSIFICATION, ORGANISATION,
-- ORGANISATION_LOCATION, ORGANISATION_CONTACT, SERVICE, ORGANISATION_SERVICE,
-- ORGANISATION_OPENING_TIME, FACILITY, ORGANISATION_FACILITY, COMPONENT_UPDATE.
--
-- Run this whole file in MySQL Workbench (a schema tab, or
-- Server > Data Import isn't needed -- just open the file and hit the
-- lightning-bolt "Execute" button) before running ingest_dohs_to_mysql.py.

CREATE DATABASE IF NOT EXISTS nhs_directory
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE nhs_directory;

-- Every id below is a deterministic string (derived from the source data),
-- not an auto-increment surrogate. That makes the loader idempotent: running
-- ingest_dohs_to_mysql.py twice updates rows in place instead of duplicating
-- them.

-- OrganisationTypeId ("TRU", "GDOS", ...) is not a 1:1 code -> name mapping in
-- the source data: e.g. "TRU" covers "Acute Trust", "MentalHealth Trust",
-- "SocialCare Trust" and others. The natural key is therefore the
-- (code, name) pair, not the code alone.
CREATE TABLE IF NOT EXISTS organisation_classification (
    classification_id      INT AUTO_INCREMENT PRIMARY KEY,
    organisation_type_code VARCHAR(20)  NOT NULL,
    organisation_type_name VARCHAR(150) NOT NULL,
    UNIQUE KEY uq_organisation_type (organisation_type_code, organisation_type_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation (
    organisation_id       VARCHAR(80)  NOT NULL PRIMARY KEY,
    source_record_key     VARCHAR(80)  NOT NULL,
    ods_code               VARCHAR(64),
    classification_id      INT,
    organisation_name      VARCHAR(255) NOT NULL,
    organisation_subtype   VARCHAR(100),
    organisation_status    VARCHAR(50),
    parent_organisation_id VARCHAR(80),
    eps_enabled            BOOLEAN,
    UNIQUE KEY uq_source_record_key (source_record_key),
    KEY ix_organisation_ods_code (ods_code),
    KEY ix_organisation_parent (parent_organisation_id),
    CONSTRAINT fk_organisation_classification
        FOREIGN KEY (classification_id)
        REFERENCES organisation_classification (classification_id)
        ON DELETE SET NULL,
    CONSTRAINT fk_organisation_parent
        FOREIGN KEY (parent_organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation_location (
    location_id     VARCHAR(90) NOT NULL PRIMARY KEY,
    organisation_id VARCHAR(80) NOT NULL,
    address_line_1  VARCHAR(255),
    address_line_2  VARCHAR(255),
    address_line_3  VARCHAR(255),
    city            VARCHAR(120),
    county          VARCHAR(120),
    postcode        VARCHAR(20),
    country         VARCHAR(100),
    latitude        DECIMAL(9, 6),
    longitude       DECIMAL(9, 6),
    UNIQUE KEY uq_organisation_location (organisation_id),
    CONSTRAINT fk_location_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation_contact (
    contact_id      VARCHAR(100) NOT NULL PRIMARY KEY,
    organisation_id VARCHAR(80)  NOT NULL,
    contact_type    VARCHAR(50),
    contact_context VARCHAR(120),
    contact_value   VARCHAR(500),
    KEY ix_contact_organisation (organisation_id),
    CONSTRAINT fk_contact_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS service (
    service_id   VARCHAR(40)  NOT NULL PRIMARY KEY,
    service_code VARCHAR(50),
    service_name VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_service_code_name (service_code, service_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation_service (
    organisation_service_id VARCHAR(130) NOT NULL PRIMARY KEY,
    organisation_id          VARCHAR(80) NOT NULL,
    service_id               VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_organisation_service (organisation_id, service_id),
    KEY ix_organisation_service_service (service_id),
    CONSTRAINT fk_organisation_service_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_organisation_service_service
        FOREIGN KEY (service_id)
        REFERENCES service (service_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation_opening_time (
    opening_time_id VARCHAR(110) NOT NULL PRIMARY KEY,
    organisation_id  VARCHAR(80) NOT NULL,
    opening_type     VARCHAR(50),
    day_of_week      VARCHAR(20),
    start_time       TIME,
    end_time         TIME,
    exception_date   DATE,
    closed           BOOLEAN NOT NULL DEFAULT FALSE,
    KEY ix_opening_time_organisation (organisation_id),
    CONSTRAINT fk_opening_time_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS facility (
    facility_id          VARCHAR(40)  NOT NULL PRIMARY KEY,
    source_facility_code VARCHAR(40)  NOT NULL,
    facility_category    VARCHAR(150),
    facility_name        VARCHAR(150) NOT NULL,
    UNIQUE KEY uq_source_facility_code (source_facility_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS organisation_facility (
    organisation_facility_id VARCHAR(130) NOT NULL PRIMARY KEY,
    organisation_id           VARCHAR(80) NOT NULL,
    facility_id               VARCHAR(40) NOT NULL,
    availability_status       VARCHAR(20),
    UNIQUE KEY uq_organisation_facility (organisation_id, facility_id),
    KEY ix_organisation_facility_facility (facility_id),
    CONSTRAINT fk_organisation_facility_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_organisation_facility_facility
        FOREIGN KEY (facility_id)
        REFERENCES facility (facility_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS component_update (
    component_update_id VARCHAR(150) NOT NULL PRIMARY KEY,
    organisation_id       VARCHAR(80) NOT NULL,
    component_name        VARCHAR(100) NOT NULL,
    last_updated_at        DATETIME,
    KEY ix_component_update_organisation (organisation_id),
    CONSTRAINT fk_component_update_organisation
        FOREIGN KEY (organisation_id)
        REFERENCES organisation (organisation_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;
