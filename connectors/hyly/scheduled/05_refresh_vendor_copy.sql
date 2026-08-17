-- 05:00 America/Phoenix — pull Hyly's multi-touch table into our own project.
--
-- Replaces the manual one-shot copy taken 2026-08-11 that nobody re-ran, which
-- is why the portal's funnel stops at 2026-08-10.
--
-- Full replace rather than incremental: the table is ~50 MB / 330k rows, so a
-- daily rebuild costs fractions of a cent and removes a whole class of bugs
-- (late-arriving rows, milestone timestamps being backfilled on existing
-- contacts, contacts being deleted on Hyly's side). Revisit only if it grows
-- past a few GB.
--
-- BLOCKED until Hyly extends its IAM grant from Kyle.Shipp@rpmliving.com to
-- rpm-portal@rpm-portal-492523.iam.gserviceaccount.com. This schedule is
-- created DISABLED so it cannot fail silently in the meantime.

CREATE OR REPLACE TABLE `hyly-data.GA4_Hyly.ga_hyly_mti`
AS
SELECT * FROM `gds-prototype-20190629.rpm_living_nrt.ga_hyly_mti`
