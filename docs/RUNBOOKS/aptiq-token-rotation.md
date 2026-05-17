# Runbook: AptIQ API token rotation

**Audience:** dev/ops (not AM-facing per the 2026-05-16 design decision).
**Frequency:** every 30 days (vendor-enforced expiry, can't be extended).
**Risk if missed:** silent degradation — every endpoint that reads AptIQ
returns None / "no data" without errors.

## Background

ApartmentIQ issues JWT API tokens that always expire 30 days after issue.
Per ADR 0012, we monitor expiry, post warning Slack messages at 14/3/0
days remaining, and support a zero-downtime rotation flow via a standby
token slot.

## Token expiry monitor

A Render Cron Job runs `python3 webhook-server/aptiq_token_monitor.py`
weekly. It:

1. Decodes the JWT in `ApartmentIQ_Token` (and `ApartmentIQ_Token_Standby`
   if set) — extracts the `exp` claim
2. Emits `loop_event(stage='ops', event_type='aptiq_token_checked')` with
   `days_left` as magnitude
3. When `days_left <= 14`: also emits `aptiq_token_warning` + posts to
   `#digital-ops` Slack
4. When `days_left <= 3`: emits `aptiq_token_critical` + posts to Slack

To run manually:
```bash
cd ~/project/src
python3 webhook-server/aptiq_token_monitor.py
# Exit code: 0=ok | 1=warning | 2=critical/expired
```

## Rotation flow (zero downtime)

### 1. Get a fresh token

Email AptIQ support requesting a new JWT for account 9902353. Mention the
bulk_api/jobs scope (we need it for historical pulls).

Save the response somewhere safe but NOT in chat / git.

### 2. Set the new token as STANDBY first

Render → `rpm-portal-server` → Environment tab → add or update
`ApartmentIQ_Token_Standby` with the new value. Save.

Render will restart the service. Wait ~2 minutes for the new container
to be live.

### 3. Verify standby works

The client picks `ApartmentIQ_Token` (primary) over standby by default,
so standby alone won't be exercised. Run the monitor to confirm both
tokens decode:

```bash
python3 webhook-server/aptiq_token_monitor.py
# Look for two lines: primary status=... and standby status=...
```

If you want to actually exercise the standby (run an AptIQ API call
through it), temporarily clear `ApartmentIQ_Token` in a Render shell
(`export ApartmentIQ_Token=""`) and hit any AptIQ endpoint. The client
will fall through to standby.

### 4. Promote standby → primary

Render → Environment → copy `ApartmentIQ_Token_Standby` value to
`ApartmentIQ_Token`. Clear `ApartmentIQ_Token_Standby` (leave it empty).
Save. Render restarts.

### 5. Verify primary works

Hit an endpoint that reads AptIQ:
```bash
INTERNAL_API_KEY=$(grep ^INTERNAL_API_KEY .env | cut -d= -f2-)
curl -sX POST \
  -H "X-Internal-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company_id":"10560269171","months_back":1,"dry_run":true,"async":false}' \
  https://rpm-portal-server.onrender.com/api/internal/aptiq-backfill-history \
  | python3 -m json.tool
```
Should return `months_returned > 0` (or a real error other than 401).

### 6. Old token expires harmlessly

The old token's remaining ~30 days run out without affecting anything,
since we've moved off it.

## When the monitor cron warns at 14 days

Slack message looks like:
> ⚠️ AptIQ token primary status: *WARNING* (12.3 days left, expires 2026-05-29T...)

Action: schedule a calendar block for token rotation within 7 days. No
emergency — primary is still working.

## When the monitor cron warns at 3 days

Slack message looks like:
> ⚠️ AptIQ token primary status: *CRITICAL* (2.1 days left, expires 2026-05-18T...)

Action: rotate today. Use the standby flow so there's no downtime even
if email response is slow.

## When primary is EXPIRED but standby is OK

The client's `_active_token()` falls through automatically — every AptIQ
call uses the standby. No action needed for the service to keep working,
but you should still rotate the primary to keep the standby slot
available for the next cycle.

## When both are expired

Catastrophic but recoverable. The service's CSV fallback
(`_csv_snapshot_fallback`) covers current-day reads from the AptIQ daily
sheet at `APT_IQ_DAILY_SHEET_URL`, so current data keeps flowing. The
historical pull endpoints will return empty.

Action: get a fresh token ASAP; once installed, manually trigger the
backfill flow to fill in any month rows missed during the outage.

## References

- ADR 0012 — AptIQ token rotation pattern
- `webhook-server/aptiq_token_monitor.py` — the monitor script
- `webhook-server/apartmentiq_client.py` — `_active_token()` failover
