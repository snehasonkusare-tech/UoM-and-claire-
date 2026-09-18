-- Complex query examples against nhs_directory. Each one exercises a
-- different SQL feature (CTE, window function, recursive CTE, correlated
-- subquery) across multiple joined tables. Run in Workbench against the
-- same connection used for schema.sql / ingest_dohs_to_mysql.py.

USE nhs_directory;

-- Q1: Top 3 organisations per organisation type, ranked by how many
-- distinct services + available facilities they offer.
-- Demonstrates: CTEs, window function (ROW_NUMBER), filtering on a window
-- function result -- and a deliberately fan-out-free join shape.
--
-- organisation_service and organisation_facility are both one-to-many off
-- organisation; joining them directly in the same query multiplies rows
-- (an org with 20 services x 30 facilities produces 600 intermediate rows
-- before aggregation), which is slow at this row count. Aggregating each
-- side separately first, then joining the two 1-per-org summaries, avoids
-- that. ROW_NUMBER (rather than RANK) also guarantees exactly 3 rows per
-- type even when many organisations tie on score.
WITH service_counts AS (
    SELECT organisation_id, COUNT(DISTINCT service_id) AS service_count
    FROM organisation_service
    GROUP BY organisation_id
),
facility_counts AS (
    SELECT organisation_id, COUNT(DISTINCT facility_id) AS facility_count
    FROM organisation_facility
    WHERE availability_status = 'Yes'
    GROUP BY organisation_id
),
org_stats AS (
    SELECT
        o.organisation_id,
        o.organisation_name,
        c.organisation_type_name,
        COALESCE(sc.service_count, 0) AS service_count,
        COALESCE(fc.facility_count, 0) AS facility_count
    FROM organisation o
    JOIN organisation_classification c ON c.classification_id = o.classification_id
    LEFT JOIN service_counts sc ON sc.organisation_id = o.organisation_id
    LEFT JOIN facility_counts fc ON fc.organisation_id = o.organisation_id
),
ranked AS (
    SELECT
        org_stats.*,
        ROW_NUMBER() OVER (
            PARTITION BY organisation_type_name
            ORDER BY service_count + facility_count DESC, organisation_name
        ) AS rn
    FROM org_stats
)
SELECT organisation_type_name, organisation_name, service_count, facility_count
FROM ranked
WHERE rn <= 3
ORDER BY organisation_type_name, rn;


-- Q2: How many levels deep does the parent/child organisation hierarchy go?
-- Demonstrates: recursive CTE walking organisation.parent_organisation_id.
WITH RECURSIVE org_chain AS (
    SELECT
        organisation_id,
        parent_organisation_id,
        1 AS depth
    FROM organisation
    WHERE parent_organisation_id IS NULL

    UNION ALL

    SELECT
        o.organisation_id,
        o.parent_organisation_id,
        oc.depth + 1
    FROM organisation o
    JOIN org_chain oc ON o.parent_organisation_id = oc.organisation_id
)
SELECT depth, COUNT(*) AS organisations_at_depth
FROM org_chain
GROUP BY depth
ORDER BY depth;


-- Q3: Organisations that report General opening hours on all 7 days,
-- with their earliest opening time and latest closing time.
-- Demonstrates: join + filter + GROUP BY ... HAVING on an aggregate.
SELECT
    o.organisation_id,
    o.organisation_name,
    COUNT(DISTINCT ot.day_of_week) AS days_open,
    MIN(ot.start_time) AS earliest_open,
    MAX(ot.end_time) AS latest_close
FROM organisation o
JOIN organisation_opening_time ot
    ON ot.organisation_id = o.organisation_id
    AND ot.opening_type = 'General'
    AND ot.closed = 0
    AND ot.day_of_week IS NOT NULL
GROUP BY o.organisation_id, o.organisation_name
HAVING days_open = 7
ORDER BY earliest_open
LIMIT 20;


-- Q4: Organisations that offer a vaccination-related service AND have
-- wheelchair access, with their address.
-- Demonstrates: two correlated EXISTS subqueries against different child
-- tables on the same outer row.
SELECT o.organisation_id, o.organisation_name, l.city, l.postcode
FROM organisation o
JOIN organisation_location l ON l.organisation_id = o.organisation_id
WHERE EXISTS (
    SELECT 1
    FROM organisation_service os
    JOIN service s ON s.service_id = os.service_id
    WHERE os.organisation_id = o.organisation_id
      AND s.service_name LIKE '%vaccination%'
)
AND EXISTS (
    SELECT 1
    FROM organisation_facility ofac
    JOIN facility f ON f.facility_id = ofac.facility_id
    WHERE ofac.organisation_id = o.organisation_id
      AND f.facility_name = 'Wheelchair access'
      AND ofac.availability_status = 'Yes'
)
ORDER BY o.organisation_name
LIMIT 20;
