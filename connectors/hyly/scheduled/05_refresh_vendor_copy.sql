-- 05:00 America/Phoenix — pull Hyly's multi-touch data into our own project.
--
-- Replaces the manual one-shot copy taken 2026-08-11 that nobody re-ran.
--
-- SAFETY GUARD, and it is not theoretical. Hyly's pipeline truncates and
-- rebuilds tables in place: their dataset is full of 0-row objects sitting
-- beside `*_bckup_` copies holding the real data. A blind CREATE OR REPLACE
-- that happened to run mid-rebuild would silently replace a good copy with an
-- empty one, and the portal would show a healthy, empty funnel. So the job
-- refuses to overwrite when the source looks wrong, and fails loudly instead.
--
-- Note `ga_hyly_mti` is a VIEW on their side, not a table — its row count only
-- exists at query time, which is why the guard counts rather than reading
-- metadata.
--
-- Full replace rather than incremental: ~350k rows, so a daily rebuild costs
-- fractions of a cent and removes a class of bugs (late-arriving rows,
-- milestones backfilled onto existing contacts, contacts deleted upstream).

BEGIN
  DECLARE src_rows INT64 DEFAULT 0;
  DECLARE dst_rows INT64 DEFAULT 0;

  SET src_rows = (SELECT COUNT(*) FROM `gds-prototype-20190629.rpm_living_nrt.ga_hyly_mti`);
  SET dst_rows = IFNULL((
    SELECT row_count FROM `hyly-data.GA4_Hyly.__TABLES__` WHERE table_id = 'ga_hyly_mti'
  ), 0);

  IF src_rows = 0 THEN
    RAISE USING MESSAGE =
      'Hyly source ga_hyly_mti returned 0 rows — refusing to replace the local copy. Their rebuild is probably mid-flight; check before re-running.';
  END IF;

  IF dst_rows > 0 AND src_rows < DIV(dst_rows, 2) THEN
    RAISE USING MESSAGE = FORMAT(
      'Hyly source shrank from %d to %d rows (>50%% drop) — refusing to replace. Investigate upstream before re-running.',
      dst_rows, src_rows);
  END IF;

  CREATE OR REPLACE TABLE `hyly-data.GA4_Hyly.ga_hyly_mti`
  AS SELECT * FROM `gds-prototype-20190629.rpm_living_nrt.ga_hyly_mti`;
END;
