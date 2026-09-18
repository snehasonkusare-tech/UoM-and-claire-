-- Sanity checks for the nhs_directory database after running
-- ingest_dohs_to_mysql.py. Open this file in MySQL Workbench (connected to
-- the same server) and run it as a script (the lightning-bolt icon, or
-- Query > Execute (All or Selection)).
--
-- Every "should be 0" check finding a non-zero row count means something is
-- wrong with the load; everything else is descriptive (row counts, samples)
-- so you can eyeball that the data looks right.

USE nhs_directory;

-- 1. Row counts per table -----------------------------------------------
SELECT 'organisation_classification' AS table_name, COUNT(*) AS row_count FROM organisation_classification
UNION ALL SELECT 'organisation', COUNT(*) FROM organisation
UNION ALL SELECT 'organisation_location', COUNT(*) FROM organisation_location
UNION ALL SELECT 'organisation_contact', COUNT(*) FROM organisation_contact
UNION ALL SELECT 'service', COUNT(*) FROM service
UNION ALL SELECT 'organisation_service', COUNT(*) FROM organisation_service
UNION ALL SELECT 'organisation_opening_time', COUNT(*) FROM organisation_opening_time
UNION ALL SELECT 'facility', COUNT(*) FROM facility
UNION ALL SELECT 'organisation_facility', COUNT(*) FROM organisation_facility
UNION ALL SELECT 'component_update', COUNT(*) FROM component_update;

-- 2. Every organisation should have exactly one location row ------------
-- (organisation_location.organisation_id is UNIQUE, so "should be 0" here
-- means no organisation is missing its location row.)
SELECT COUNT(*) AS organisations_missing_location
FROM organisation o
LEFT JOIN organisation_location l ON l.organisation_id = o.organisation_id
WHERE l.organisation_id IS NULL;

-- 3. No organisation should be its own parent ----------------------------
SELECT COUNT(*) AS self_parented_organisations
FROM organisation
WHERE parent_organisation_id = organisation_id;

-- 4. No dangling foreign keys (belt-and-braces on top of the FK constraints) --
SELECT COUNT(*) AS orphaned_organisation_service
FROM organisation_service os
LEFT JOIN organisation o ON o.organisation_id = os.organisation_id
LEFT JOIN service s ON s.service_id = os.service_id
WHERE o.organisation_id IS NULL OR s.service_id IS NULL;

SELECT COUNT(*) AS orphaned_organisation_facility
FROM organisation_facility ofac
LEFT JOIN organisation o ON o.organisation_id = ofac.organisation_id
LEFT JOIN facility f ON f.facility_id = ofac.facility_id
WHERE o.organisation_id IS NULL OR f.facility_id IS NULL;

-- 5. Case-only duplicates should no longer exist in the service catalog --
-- (confirms the collation_key fix: every case-folded name maps to exactly
-- one service_id, so this should return 0 rows.)
SELECT LOWER(TRIM(service_name)) AS folded_name, COUNT(DISTINCT service_id) AS distinct_ids
FROM service
GROUP BY folded_name
HAVING distinct_ids > 1;

-- 6. Classification codes that map to more than one name (expected: real --
-- NHS data, e.g. "TRU" legitimately covers several trust types) ----------
SELECT organisation_type_code, COUNT(*) AS distinct_names, GROUP_CONCAT(organisation_type_name)
FROM organisation_classification
GROUP BY organisation_type_code
HAVING distinct_names > 1;

-- 7. How many organisations resolved a parent vs couldn't -----------------
-- (a parent ODS code with no matching organisation row -- e.g. an ICB that
-- isn't itself listed in this directory export -- is expected, not a bug.)
SELECT
    SUM(parent_organisation_id IS NOT NULL) AS parent_resolved,
    SUM(parent_organisation_id IS NULL) AS parent_unresolved_or_none
FROM organisation;

-- 8. Organisation counts by type, most common first -----------------------
SELECT c.organisation_type_name, COUNT(*) AS organisation_count
FROM organisation o
JOIN organisation_classification c ON c.classification_id = o.classification_id
GROUP BY c.organisation_type_name
ORDER BY organisation_count DESC;

-- 9. Spot-check: one organisation with its full picture --------------------
-- Swap the organisation_id below for any id you want to inspect.
SET @sample_org := (SELECT organisation_id FROM organisation LIMIT 1);

SELECT * FROM organisation WHERE organisation_id = @sample_org;
SELECT * FROM organisation_location WHERE organisation_id = @sample_org;
SELECT * FROM organisation_contact WHERE organisation_id = @sample_org;
SELECT s.service_name FROM organisation_service os JOIN service s USING (service_id) WHERE os.organisation_id = @sample_org;
SELECT f.facility_name, f.facility_category, ofac.availability_status
FROM organisation_facility ofac JOIN facility f USING (facility_id) WHERE ofac.organisation_id = @sample_org;
SELECT day_of_week, start_time, end_time, closed FROM organisation_opening_time WHERE organisation_id = @sample_org;
SELECT component_name, last_updated_at FROM component_update WHERE organisation_id = @sample_org;
