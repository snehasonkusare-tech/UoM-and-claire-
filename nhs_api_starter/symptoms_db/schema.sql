-- Symptom scrape warehouse schema.
-- Matches the ER diagram: SCRAPE_RUNS (1) --captures--> (*) SYMPTOMS,
-- SCRAPE_RUNS (1) --records--> (*) SCRAPE_ERRORS.
--
-- Run this whole file in MySQL Workbench (a schema tab, or the
-- lightning-bolt "Execute" button) before running
-- ingest_symptoms_to_mysql.py. Lives in its own nhs_symptoms database,
-- separate from the DoHS organisation/service data in nhs_directory
-- (db/schema.sql) -- the two domains don't share any tables or foreign keys.
--
-- article_text_embedding is declared JSON, not VECTOR: this server is MySQL
-- 8.0 (checked via SELECT VERSION()), and the native VECTOR type only
-- arrived in MySQL 9.0. A JSON array of floats is the portable stand-in --
-- readable, upgrade-safe, and fine at this table's scale (a few hundred to
-- a few thousand symptom pages).

CREATE DATABASE IF NOT EXISTS nhs_symptoms
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE nhs_symptoms;

CREATE TABLE IF NOT EXISTS scrape_runs (
    scrape_run_id    VARCHAR(64)  NOT NULL PRIMARY KEY,
    scraped_at_utc    DATETIME     NOT NULL,
    base_url          VARCHAR(255) NOT NULL,
    scrape_method     VARCHAR(50)  NOT NULL,
    pages_found       INT          NOT NULL DEFAULT 0,
    pages_selected    INT          NOT NULL DEFAULT 0,
    pages_succeeded   INT          NOT NULL DEFAULT 0,
    pages_failed      INT          NOT NULL DEFAULT 0,
    complete          BOOLEAN      NOT NULL DEFAULT FALSE
) ENGINE=InnoDB;

-- symptom_id is deterministic (hashed from the symptom's URL), so re-running
-- the ingest against the same NHS pages updates rows in place under a new
-- scrape_run_id instead of duplicating them -- the loader stays idempotent,
-- same as ingest_dohs_to_mysql.py.
CREATE TABLE IF NOT EXISTS symptoms (
    symptom_id             VARCHAR(64)  NOT NULL PRIMARY KEY,
    scrape_run_id          VARCHAR(64)  NOT NULL,
    symptom_name            VARCHAR(255) NOT NULL,
    url                     VARCHAR(500) NOT NULL,
    description             TEXT,
    reviewed_date           DATE,
    next_reviewed_date      DATE,
    article_text            LONGTEXT,
    article_text_embedding  JSON,
    embedding_model         VARCHAR(100),
    page_scraped_at_utc     DATETIME,
    UNIQUE KEY uq_symptoms_url (url),
    KEY ix_symptoms_scrape_run (scrape_run_id),
    CONSTRAINT fk_symptoms_scrape_run
        FOREIGN KEY (scrape_run_id)
        REFERENCES scrape_runs (scrape_run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scrape_errors (
    error_id        VARCHAR(80)  NOT NULL PRIMARY KEY,
    scrape_run_id    VARCHAR(64)  NOT NULL,
    url              VARCHAR(500) NOT NULL,
    http_status      INT,
    error_message    VARCHAR(1000),
    recorded_at_utc  DATETIME     NOT NULL,
    KEY ix_scrape_errors_scrape_run (scrape_run_id),
    CONSTRAINT fk_scrape_errors_scrape_run
        FOREIGN KEY (scrape_run_id)
        REFERENCES scrape_runs (scrape_run_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;
