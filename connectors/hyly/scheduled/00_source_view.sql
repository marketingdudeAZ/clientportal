-- ga_hyly_mti_current — the single place that decides which copy of Hyly's
-- multi-touch table is authoritative.
--
-- Everything downstream (the 06:00 rollup) reads THIS view, never a concrete
-- table. That means switching from the frozen 2026-08-11 RPM snapshot to a
-- live vendor-refreshed copy is a one-line change here, with no edit to the
-- rollup, the scheduled queries, or the application.
--
-- Today  : points at data-and-reporting-483421.hyly.ga_hyly_mti
--          (RPM's one-shot copy; created == modified == 2026-08-11, frozen)
-- Target : points at hyly-data.GA4_Hyly.ga_hyly_mti
--          (refreshed 05:00 daily from gds-prototype-20190629.rpm_living_nrt
--           once Hyly grants the service account — see ADR 0022 open item 1)
--
-- Flip by running scripts/setup_hyly_schedules.py --promote-vendor-source.

CREATE OR REPLACE VIEW `hyly-data.GA4_Hyly.ga_hyly_mti_current` AS
SELECT * FROM `{source_table}`
