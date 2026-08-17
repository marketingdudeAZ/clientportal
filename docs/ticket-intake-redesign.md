# Portal Ticket Intake — Redesign & Implementation Brief

**Status:** Ready to build
**Date:** 2026-08-17
**Branch:** `claude/portal-ticketing-redesign-sjcalh`
**Supersedes the open questions in:** `docs/ticket-page-scope.md` (2026-07-15)

> This document is written to be executed. It contains the root-cause
> analysis, the target field manifests for all 8 ticket types, and a phased
> task list with acceptance criteria. Phases 1–2 are unblocked and can be
> built as specified. Phases 3–5 carry assumptions flagged in §9 — build to
> the stated default and leave the alternative reachable by config.

---

## 1. TL;DR

Two problems, one small and one worth the effort.

**Small:** the portal renders ~105 fields — the union of every custom field in
the ClickUp space — on 4 of 6 client-facing ticket types, and renders *zero*
fields on a 5th. Root cause is a single wrong API assumption in
`webhook-server/portal_tickets.py:120`.

**Worth the effort:** even a correct field-for-field rebuild would keep asking
requesters for things we already hold — property identity (on the HubSpot
company record, already resolved before the form draws) and ~10 marketing
fields the Community Brief owns as source of truth. Those answers currently go
into ClickUp and die there; nothing writes back to `fluency_*_override`.

Target end state: every field on every form is classified into exactly one of
three tiers, and **only the third tier is an input**.

| Tier | Rule | Source |
|---|---|---|
| **KNOWN** | Never ask | HubSpot company record, resolved via `uuid` |
| **FILE** | Confirm, don't retype — writes back | Community Brief `fluency_*_override` |
| **ASK** | The only real inputs | New to this request |

Result across the client-facing types:

| Request type | Renders now | KNOWN | FILE | ASK |
|---|---:|---:|---:|---:|
| General Ticket | ~105 | 5 | 0 | **4** |
| Ad Updates: Photos & New Specials | 0 | 6 | 0 | **5** |
| Rebrands | ~105 | 6 | 0 | **7** |
| Digital Marketing Review | 10 | 5 | 4† | **13** |
| Budget Changes | ~105 | 9 | 7 | **14** |
| New Account Onboarding | ~105 | 16 | 9 | **20** |

† Occupancy, ATR, trend and budget-changed come from the portal's own metrics,
not the brief. Same principle, different source — see §6.

---

## 2. Root cause

`webhook-server/portal_tickets.py:120`:

```python
def form_schema(list_id: str) -> list[dict[str, Any]]:
    """Client-facing form fields for a list — ClickUp fields minus the ones we
    pre-fill from the property record."""
    return [
        _shape_field(f)
        for f in clickup_client.get_list_fields(list_id)   # <-- GET /list/{id}/field
        if not _is_prefill(f.get("name"))
    ]
```

In ClickUp, **custom fields are defined at the space/folder level, not the list
level**. `GET /list/{id}/field` returns every field *available to* the list —
which, when all 8 ticket lists live in one space, is the union of all of them.

The thing that decides which fields a form shows, in what order, with what help
text and conditional logic, is the **form view**. None of that is in the
list-field response, and ClickUp's public API does not reliably expose form-view
field configuration.

`docs/ticket-page-scope.md` §2 chose to read forms live from ClickUp so they
would never drift. The instinct is right; the endpoint it picked has no concept
of a form.

### Observable symptoms (from portal screenshots, 2026-08-17)

- **New Account Onboarding, Budget Changes, General Ticket, Rebrands** — all
  render the same ~105 fields.
- **Ad Updates: Photos & New Specials** — renders only Request Type + Subject.
  A special submitted through the portal today arrives with no special, no
  channels impacted and no end date.
- **Digital Marketing Review** — renders 10 fields, 3 of which are internal
  (`QA Status`, `Task Progress`, `Z_Requested By`), and is missing 12 of the
  questions the real Campaign Performance Review form asks.
- The dump contains 8 paid channels **twice** (currency variant for new build,
  dropdown variant for budget update): Paid Search, Google Display, PMax, Paid
  Social, Geofence, Retargeting, TikTok, Programmatic.
- Internal ops fields leak to clients: `QA Status`, `Task Progress`,
  `Z_Requester Name`, `Z_Requested By`, `Z_Client Name`, `Z_Top Competitors`,
  `Final Deliverable - SP Path`, `Type of Template`, `Resources`.
- No ordering, no sections, no help text, no SLA banner, no character limits.

---

## 3. The fix, architecturally

Keep the anti-drift goal from the scope doc; split what each side owns.

| Concern | Owner | Redeploy needed on change? |
|---|---|---|
| Which fields appear, order, sections, help text, char limits, conditional logic | **Manifest in repo** | Yes |
| Field IDs, ClickUp types, dropdown option values | **ClickUp, resolved live by name** | No |
| Drift between the two | **CI test** | Fails loudly |

So a team member editing a dropdown's options in ClickUp still flows through
with no redeploy. Adding or removing a field from a form requires a manifest
change — and the drift test tells you when that happened rather than the field
silently vanishing.

### Manifest shape

Add to `webhook-server/config.py`, keyed by the existing `PORTAL_TICKET_TYPES[].key`:

```python
# Per-type form manifest. `name` MUST match the ClickUp custom-field name
# exactly — field ids are resolved live by name at render and submit time
# (see portal_tickets.form_schema). `tier` is one of:
#   "known" — resolved from the property record, never rendered as an input
#   "file"  — pre-filled from the Community Brief, confirm-or-update
#   "ask"   — a real input
# `source` names the HubSpot property (tier known) or fluency_* override
# (tier file) the value comes from.
PORTAL_TICKET_FORMS = {
    "<type_key>": {
        "sla": "Please allow 5 - 7 business days ...",
        "prereqs": ["Property website is live.", ...],     # rendered as a checklist
        "notes":   ["If this is a disposition/cancellation, submit ...", ...],
        "name_pattern": "[Property Name] - General Ticket - [Issue / Inquiry]",
        "sections": [
            {
                "title": "Property Information",
                "help":  "",
                "fields": [
                    {"name": "Property Code", "tier": "known",
                     "source": "property_code"},
                    {"name": "Top Amenities / Selling Points", "tier": "file",
                     "source": "fluency_amenities_override"},
                    {"name": "What is the new/updated special?", "tier": "ask",
                     "required": True, "maxlength": 90,
                     "help": "Ex: 2 bedrooms starting at $1500 through July 15th.",
                     "validate": "no_caps_no_punct_except_period"},
                ],
            },
        ],
    },
}
```

Fields with `tier` of `known` or `file` still get **submitted** to ClickUp —
they're just not rendered as blank inputs. `known` is filled server-side;
`file` is rendered pre-filled with a confirm-or-update control.

---

## 4. Field manifests — the 8 ClickUp forms

Transcribed from the live ClickUp forms on 2026-08-17. `*` = required in
ClickUp. Tier column is the target classification.

### 4.1 `new_account_build` — Digital Marketing New Account Build
Form slug `8cjaf2c-19771` · **SLA 5–7 business days**

Prereqs (render as a checklist before the form): property website is live ·
property images received (min 2–3) · property logo received · property code
received (in SalesForce) · P-Code received in RentCafe (paid products ONLY) ·
tracking and UTM codes received · IO has been signed.

Note: *for new properties coming online, NOT for budget changes. If budget
recommendations are needed, submit a General ticket.*

| # | Field | ClickUp type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| **Property Information** |
| 1 | Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Earliest Launch Date | select | * | ask | 'Future Date' if >7 days out |
| 4 | Property Address | text | * | known | HubSpot address |
| 5 | Property URL | url | * | known | `website` |
| 6 | Property Type | select | * | known | `property_type` |
| 7 | Property Email Address | email | * | known | HubSpot company |
| 8 | Property Code | text | * | known | `property_code` |
| 9 | Property Website Platform | text | * | ask | ex. RentCafe, Jonah, Perq, Knock, RealPage, Fervor |
| 10 | Property Website Bot | text | * | ask | ex. CRMIQ, Knock, Funnel, Client Hosted, None |
| 11 | Property Status | select | * | known | `fluency_lifecycle_state` |
| 12 | Client / Portfolio Name | text | * | known | HubSpot company |
| 13 | Account Manager | select | * | known | derive from `market` |
| 14 | Digital Region | select | * | known | `market` |
| 15 | RM's Email | email | * | known | `rm_email` — IO approver |
| 16 | RVP's Email | email | * | known | `rvp_email` / `marketing_rvp_email` |
| **Property Details** |
| 17 | Top Amenities / Selling Points | textarea | * | file | `fluency_amenities_override`, `fluency_marketed_amenity_names_override` |
| 18 | Local Hotspots / Attractions (List at least 4) | textarea | * | file | `fluency_landmarks_override`, `fluency_neighborhood_highlights_override` |
| 19 | Campaign Focus | textarea | * | ask | main goal; specific floor plans / layouts |
| 20 | Top Competitors | textarea | * | file | `fluency_competitors_override` |
| 21 | Local / Colloquial Terms | text | * | file | `fluency_neighborhood_override`, `fluency_nearby_neighborhoods_override` |
| 22 | Keywords to Target | text | * | ask | |
| 23 | Voice & Tone | text | | file | `fluency_voice_tier_override` |
| 24 | Target Audience | text | * | file | `fluency_motivations_considerations_override` |
| 25 | Property Floor Plan Type | multiselect | * | file | `fluency_floor_plans_override` |
| 26 | SharePoint URL | url | | ask | |
| **Account Budget → Paid** — *mgmt fee 20% or $250, whichever greater* |
| 27 | Paid Search | currency | | ask | |
| 28 | Google Display | currency | | ask | creative required; if not completed submit CSR |
| 29 | PMax | currency | | ask | |
| 30 | Paid Social | currency | | ask | |
| 31 | Geofence | currency | | ask | $250 one-time setup fee |
| 32 | Retargeting | currency | | ask | |
| 33 | TikTok | currency | | ask | |
| 34 | Programmatic | currency | | ask | |
| **SEO** |
| 35 | SEO Package | select | | ask | |
| **Email & Social** |
| 36 | Email Drip Campaign | select | | ask | $125/mo + $225 one-time setup |
| 37 | eBlast | checkbox | | ask | $700 one-time fee |
| 38 | Organic Social | select | | ask | |
| **Tracking Information** |
| 39 | Property Tracking Information | textarea | * | ask | |
| 40 | Is there anything else that would be helpful for us to know? | textarea | | ask | |

→ **16 known · 9 file · 20 ask** (2 of the 45 ClickUp inputs are the SLA/prereq banner)

---

### 4.2 `budget_update` — Digital Marketing Budget Update
Form slug `8cjaf2c-20971` · **SLA 5–7 business days**

Notes: *for properties already online. SLAs will not supersede best practices
(product cancellations done at EOM). Budget Update tickets that involve a new
channel build for a paid product must also collect build pre-reqs. Dispo →
Dispo ticket. Budget recommendations → General ticket.*

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| **Property Information** |
| 1 | Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Earliest Launch Date | select | * | ask | ASAP defaults to SLA turn times |
| 4 | Property URL | url | * | known | `website` |
| 5 | Property Code | text | * | known | `property_code` |
| 6 | Account Manager | select | * | known | derive from `market` |
| 7 | Digital Region | select | * | known | `market` |
| 8 | RM's Email | email | * | known | `rm_email` |
| 9 | RVP's Email | email | * | known | `rvp_email` |
| **Property Information — "if any of the following has changed"** (all optional) |
| 10 | Top Amenities / Selling Points | textarea | | file | `fluency_amenities_override` |
| 11 | Local Hotspots / Attractions (at least 4) | textarea | | file | `fluency_landmarks_override` |
| 12 | Campaign Focus | textarea | | ask | |
| 13 | Top Competitors | textarea | | file | `fluency_competitors_override` |
| 14 | Local / Colloquial Terms | text | | file | `fluency_neighborhood_override` |
| 15 | Keywords | text | | ask | |
| 16 | Target Audience | text | | file | `fluency_motivations_considerations_override` |
| 17 | SharePoint URL | url | * | ask | **required on this form** |
| **Account Budget → Paid** — dropdowns (add / remove / change), not currency |
| 18 | Paid Search | select | | ask | |
| 19 | Google Display | select | | ask | creative required; if not completed submit CSR |
| 20 | P Max | select | | ask | images + logo needed to launch a new PMax campaign |
| 21 | Paid Social | select | | ask | |
| 22 | Geofence | select | | ask | creative required |
| 23 | Retargeting | select | | ask | |
| 24 | TikTok | select | | ask | |
| 25 | Programmatic | select | | ask | |
| **SEO** |
| 26 | SEO | select | | ask | |
| **Email & Social** |
| 27 | Email Drip Campaign | select | | ask | |
| 28 | eBlast | checkbox | | ask | $700 one-time fee |
| 29 | Organic Social | select | | ask | $500 one-time setup fee |
| **Tracking Information** |
| 30 | Property Tracking Information | textarea | * | ask | |
| 31 | Is there anything else that would be helpful for us to know? | textarea | | ask | |

→ **9 known · 7 file · 14 ask**

> **Note the two channel variants.** `new_account_build` uses **currency**
> inputs; `budget_update` uses **select** (add/remove/change). These are
> separate ClickUp fields with the same display name in different lists —
> which is why the union dump shows all 8 channels twice. The manifest must
> resolve field IDs **per list**, never globally by name across the space.

---

### 4.3 `general` — General Ticket Request
Form slug `8cjaf2c-24611` · **SLA 5–7 business days**

Notes: *disposition/cancellation → Dispo ticket. Property rebrand → Rebrand
ticket.* Name pattern: `[Property Name] - General Ticket - [Issue / Inquiry]`

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Property Code | text | * | known | `property_code` |
| 4 | What is the priority of this project? | select | | ask | |
| 5 | Who is your Account Manager? | select | * | known | derive from `market` |
| 6 | Market | select | * | known | `market` |
| 7 | Category | select | * | ask | |
| 8 | File upload | attachment | | ask | |
| 9 | Is there any other information you think we should know? | textarea | | ask | |

→ **5 known · 0 file · 4 ask**

---

### 4.4 `creative_ad_copy` — Creative + Ad Copy Update Request
Form slug `8cjaf2c-2751` · **SLA 5–7 business days**

Note: *only for **live campaigns** that already have both the creative and copy
to be updated.* Name pattern: `[Property Name] - Creative / Ad Copy Update`

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Property Code | text | * | known | `property_code` |
| 4 | Property URL | url | * | known | `website` |
| 5 | Who is your Account Manager? | select | * | known | derive from `market` |
| 6 | Digital Region | select | * | known | `market` |
| **Creative + Ad Copy Information** |
| 7 | What is the new/updated special? | textarea | * | ask | **max 90 chars; no capitalization; no punctuation other than periods.** Ex: `2 bedrooms starting at $1500 through July 15th` |
| 8 | Channels / Ads Impacted | multiselect | * | ask | Display, Paid Search Ads, Paid Social Ads, P Max… |
| 9 | Special / Promotion End Date | date | * | ask | |
| 10 | SharePoint Path to New Image / Photo | url | | ask | leave blank if no image |
| 11 | Is there any other information we need to know? | textarea | | ask | |

→ **6 known · 0 file · 5 ask**

> **This is the highest-value fix in phase 1.** The portal currently renders
> *none* of fields 7–11, so every special submitted through the portal is
> unactionable on arrival.

---

### 4.5 `campaign_review` — Campaign Performance Review Request
Form slug `8cjaf2c-2771` · **SLA 5–7 business days**

Purpose banner (render verbatim): *intended to investigate a **defined
performance concern**. Not a recurring comprehensive account audit or a request
to make unspecified campaign changes. Requests such as "refresh ad copy",
"audit all keywords", or "identify any possible optimizations" may be returned
for additional detail. For standalone budget recommendations, campaign
launches, creative updates, special or concession updates, or other execution
requests, submit the applicable ticket type.*

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Property Name | text | * | known | company name |
| 2 | Property Code | text | * | known | `property_code` |
| 3 | Requested By | user | * | known | Clerk session |
| 4 | What is the priority of this request? | select | | ask | |
| 5 | Who is your Account Manager? | select | * | known | derive from `market` |
| 6 | Digital Region | select | * | known | `market` |
| **Property Review Information** |
| 7 | What is the primary reason for review? | select | * | ask | |
| 8 | What specifically changed? When did you first notice this change? | textarea | * | ask | metric(s) affected, timeframe, comparison points |
| 9 | Current Occupancy % | number | * | **file** | portal metric — auto-attach |
| 10 | Occupancy Trend | select | * | **file** | portal metric — auto-attach |
| 11 | Current ATR % | number | * | **file** | portal metric — auto-attach |
| 12 | Target Occupancy % | number | * | ask | |
| 13 | Target Occupancy Goal Date/Timeline | text | * | ask | ex: August 1st, or 2 months |
| 14 | Property Status | multiselect | * | ask | select all that apply |
| 15 | Are there any documented lead-quality concerns? | multiselect | * | ask | |
| 16 | FOR CONTEXT ONLY — active concessions or specials relevant to this review? | select | * | ask | |
| 17 | Are there particular units, price points, or renter needs relevant? | textarea | * | ask | priority floor plans, unit types, price/income requirements, immediate move-in |
| 18 | What additional channels/services are you running outside DM products? | multiselect | * | ask | |
| 19 | Which parties are concerned with campaign performance? | multiselect | * | ask | |
| 20 | Which sources have you reviewed? | multiselect | * | ask | |
| 21 | Based upon the most recent comp review, how is the property currently priced? | select | * | ask | |
| 22 | Has the property's digital budget recently changed? | select | * | **file** | Plan & Spend — auto-attach |

→ **5 known · 4 auto-attached · 13 ask**

---

### 4.6 `rebrand` — Rebrand Requests
Form slug `8cjaf2c-2811` · **SLA 5–7 business days**

Note: *rebrands will be completed as close to the requested launch date as
possible; this isn't always possible due to outside constraints.*
Name pattern: `[Property Name] - Rebrand`

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Current Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Date of Rebrand Launch | date | * | ask | |
| 4 | Who is your Account Manager? | select | * | known | derive from `market` |
| 5 | Digital Region | select | * | known | `market` |
| **Current Property Information** |
| 6 | Property Code | text | * | known | `property_code` |
| 7 | Property URL | url | * | known | `website` |
| **Rebrand Information** |
| 8 | Rebranded Property Name | text | * | ask | |
| 9 | New Website URL | url | * | ask | the URL once the rebrand takes place |
| 10 | New Property Email | email | * | ask | |
| 11 | Is the Property Website Platform changing? | select | * | ask | ex. switching from Jonah to RentCafe |
| 12 | Link to Rebrand Creative | text | * | ask | |
| 13 | Is there any other information we need to know? | textarea | | ask | |

→ **6 known · 0 file · 7 ask**

---

### 4.7 `dispo_cancel` — Dispo/Cancellation Request *(internal by default)*
Form slug `8cjaf2c-22451` · **SLA 5–7 business days once a signed IO is received**

Reminders: *dispositions completed **on the day of disposition** as outlined by
the ticket submitter · cancellations require a **30-day opt-out notice** · if
the dispo or cancellation date changes, update the team as soon as possible.*

Note: *exclusively for properties dispo'ing or completely cancelling ALL
in-house digital services. If only cancelling certain channels, submit a Budget
Update ticket instead.* Name pattern: `[Property Name] - Dispo OR Cancellation`

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Property Name | text | * | known | company name |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | Dispo/Cancellation Date | date | * | ask | cancellations need a 30-day opt-out window |
| 4 | What is the priority of this request? | select | | ask | |
| 5 | Who is your Account Manager? | select | * | known | derive from `market` |
| 6 | Digital Region | select | * | known | `market` |
| **Property Information** |
| 7 | Property Code | text | * | known | `property_code` |
| 8 | RM's Email | email | * | known | `rm_email` |
| 9 | RVP's Email | email | * | known | `rvp_email` |
| 10 | Is this a cancellation or a disposition? | select | * | ask | *Cancellation:* still under RPM Management, running digital with another agency. *Disposition:* no longer with RPM Management. |
| 11 | Is there any other information we need to know? | textarea | | ask | |

→ **7 known · 0 file · 4 ask**

---

### 4.8 `new_business` — New Business Requests *(internal by default)*
Form slug `8cjaf2c-2791` · **SLA 5–7 business days**

Note: *only fill out if pitching **new business**. If the SLA window is too
tight, contact your Account Manager directly.* Name pattern:
`[Property Name / Ownership Group] - New Business Request`

This is the one type where the property may **not exist in HubSpot yet** — it's
a prospective property. The KNOWN tier mostly does not apply; treat identity
fields as ASK unless a `company_id` is supplied.

| # | Field | Type | Req | Tier | Source / notes |
|---|---|---|---|---|---|
| 1 | Property Name | text | * | ask | prospective — may not be in HubSpot |
| 2 | Requested By | user | * | known | Clerk session |
| 3 | What is the priority of this project? | select | | ask | |
| 4 | Requested Due Date | date | * | ask | <3 days out defaults to SLAs |
| 5 | Pitch Date | date | * | ask | |
| 6 | Who is your Account Manager? | select | * | ask | no property to derive from |
| 7 | Digital Region | select | * | ask | |
| **Property Information** |
| 8 | Property URL | url | * | ask | |
| 9 | Property Address | text | * | ask | |
| 10 | Property Status | select | * | ask | |
| 11 | Property Type | select | * | ask | |
| 12 | Unit Count | number | * | ask | |
| 13 | What digital tactics are they currently running? | multiselect | * | ask | |
| 14 | Metrics that Matter | textarea | * | ask | what matters to the ownership group |
| **Competitor Information** |
| 15 | Competitor Website #1 | url | | ask | |
| 16 | Competitor Website #2 | url | | ask | |
| 17 | Competitor Website #3 | url | | ask | |
| 18 | Is there any other information you think we should know? | textarea | | ask | |

→ **1 known · 0 file · 17 ask**

---

## 5. The KNOWN tier — property identity

Currently `PORTAL_TICKET_PREFILL_FIELDS` (`config.py:185`) holds 5 entries.
Expand to the full set below. All resolve from the HubSpot company record via
the Property Resolver, addressed by `uuid` (see `IMMUTABLE_RULES.md` R1 — code
never writes `uuid`).

| ClickUp field name | HubSpot company property |
|---|---|
| Property Name / Current Property Name | company `name` |
| Property Code | `property_code` |
| Property URL | `website` |
| Property Address | HubSpot address fields |
| Property Email Address | HubSpot company email |
| Market / Digital Region / Region | `market` |
| Account Manager / Who is your Account Manager? | derived from `market` (see §5.1) |
| RM's Email | `rm_email` |
| RVP's Email | `rvp_email`, fallback `marketing_rvp_email` |
| Unit Count | `unit_count`, fallback `total_units` |
| Property Type | `property_type` |
| Property Status | `fluency_lifecycle_state` |
| Client / Portfolio Name | HubSpot company |
| Requested By / Requester Email | Clerk session identity |
| uuid / UUID | `uuid` |

**Render as a read-only summary card** at the top of the form:

> Submitting for **Vitri** · VTR001 · Dallas · Dane
> [Something wrong? Update the property profile →]

Not as disabled inputs — disabled inputs still read as "fields to deal with".

### 5.1 Kill the Account Manager lookup table

All 8 ClickUp forms embed a **screenshot of a spreadsheet** and ask the
requester to look themselves up:

| Account Manager | Digital Region(s) |
|---|---|
| Dane | Dallas |
| Dustin | Atlanta, RPMI, Southeast (TN) |
| Juliana | Houston, Midwest |
| Katie | Florida, Mid-Atlantic, West |
| Lauren | Mountain |
| Logan | Austin, San Antonio |

Encode this as a `MARKET_TO_ACCOUNT_MANAGER` map in `config.py`, derive from
`market`, and render as text ("Your account manager is Dane"). Note the
forms' own caveat: *if the property is RPMI, select RPMI rather than the
physical region; National is no longer a digital region.*

---

## 6. The FILE tier — brief-backed fields and write-back

These 10 questions are re-asked as free text on `new_account_build` and
`budget_update` while the Community Brief already owns them as source of truth
(`docs/CLIENT_BRIEF_SYSTEM.md`).

| ClickUp field | Brief override property |
|---|---|
| Top Amenities / Selling Points | `fluency_amenities_override`, `fluency_property_amenities_override`, `fluency_marketed_amenity_names_override` |
| Local Hotspots / Attractions | `fluency_landmarks_override`, `fluency_neighborhood_highlights_override` |
| Top Competitors, Competitor Website #1–3 | `fluency_competitors_override` |
| Surrounding Employers | `fluency_nearby_employers_override` |
| Property Floor Plan Type | `fluency_floor_plans_override` |
| Voice & Tone | `fluency_voice_tier_override` |
| Key Messages | `fluency_must_include_override`, `fluency_forbidden_phrases_override` |
| Target Audience | `fluency_motivations_considerations_override` |
| Local / Colloquial Terms | `fluency_neighborhood_override`, `fluency_nearby_neighborhoods_override` |
| Unit Features | `fluency_unit_features_override` |

### Render

```
Top Amenities / Selling Points
┌────────────────────────────────────────────────────┐
│ Resort-style pool · Rooftop lounge · EV charging…  │
└────────────────────────────────────────────────────┘
  From the community brief · updated 12 Jun 2026
  [ Still right ]  [ Update ]
```

### Write-back — this is what closes the loop

Today these answers land in a ClickUp ticket and stop. Nothing reaches
`fluency_*_override`, so the next ticket asks again, the daily Fluency cron
never sees the better answer, and ad copy keeps running on the stale one.

On **Update**, PATCH the override through the **existing** community-brief path
(`webhook-server/community_brief.py`, `/api/community-brief/*`) so the
override-wins model and its server-side rules are enforced in one place. Do not
write HubSpot directly from the ticket path.

### Portal-metric auto-attach (`campaign_review`)

Fields 9, 10, 11 and 22 on the Campaign Performance Review ask the requester to
hand-type Occupancy %, ATR %, occupancy trend and whether the budget changed —
while the portal sidebar is displaying `93.7% OCC` / `10.7% ATR` / `17 units`
on the same screen, and Plan & Spend holds the budget history.

Attach these as a **timestamped snapshot** from the warehouse instead of asking.
Show the values read-only with an "as of" date and an override link for the case
where the requester genuinely disagrees with the number.

---

## 7. Build phases

Each phase ships something usable on its own. Phase 1 is the bug fix.

### Phase 1 — Manifest + drift test
**Files:** `webhook-server/config.py`, `webhook-server/portal_tickets.py`,
`hubspot-cms/templates/client-portal.html`, `tests/test_portal_tickets.py`

1. Add `PORTAL_TICKET_FORMS` to `config.py` from the manifests in §4.
2. Rewrite `portal_tickets.form_schema(list_id)` → `form_schema(type_key, list_id)`:
   read the manifest, resolve each field's ClickUp id/type/options **by name
   against that list's live field defs**, drop `tier in (known, file)` from the
   rendered set, preserve manifest order and sections.
3. Extend `types_with_schema()` to return `sections`, `sla`, `prereqs`,
   `notes`, `name_pattern`.
4. `renderTicketFields()` (`client-portal.html:3723`): render section headers,
   help text, `maxlength`, and the SLA/prereq banner. Enforce the 90-char /
   no-caps / no-punctuation rule on the Creative special field client-side and
   in `_coerce()` server-side.
5. Drift test: for each configured type, assert every manifest `name` resolves
   to exactly one live ClickUp field on that list, and report any live
   **required** field missing from the manifest.

**Acceptance**
- Each of the 6 client-facing types renders only its own fields, in ClickUp order.
- `Ad Updates` renders fields 7–11 from §4.4.
- No field whose name starts with `Z_` and none of `QA Status`, `Task Progress`,
  `Final Deliverable - SP Path`, `Type of Template`, `Resources` is renderable.
- Each channel resolves to its own list's field id — currency on
  `new_account_build`, select on `budget_update`.
- Drift test passes against live ClickUp and fails when a manifest name is wrong.

### Phase 2 — Widen the KNOWN tier
**Files:** `webhook-server/config.py`, `webhook-server/portal_tickets.py`, portal template

1. Expand `PORTAL_TICKET_PREFILL_FIELDS` / `PORTAL_TICKET_PREFILL_SOURCES` to §5.
2. Add `MARKET_TO_ACCOUNT_MANAGER`; derive AM from market; drop the lookup image.
3. Replace prefilled inputs with the read-only summary card.
4. `_prefill_values()` resolves through the Property Resolver, not ad-hoc lookups.

**Acceptance**
- No identity field is a blank input on any of the 6 client-facing types.
- The created ClickUp task still carries all identity custom fields populated.
- A property missing an optional HubSpot prop degrades to an input, not an error.

### Phase 3 — FILE tier + brief write-back
**Files:** `webhook-server/portal_tickets.py`, `webhook-server/community_brief.py`, portal template

1. Resolve `tier: file` fields from their `fluency_*_override` / pipeline value,
   with source + last-updated.
2. Confirm-or-update control; on update, PATCH via the community-brief path.
3. Respect override-wins and the existing server-side edit rules.

**Acceptance**
- Updating Top Amenities inside a Budget Update ticket changes
  `fluency_amenities_override` on the company record.
- "Still right" submits the current value without a write.
- Apt IQ-sourced fields that have no override column stay read-only (per
  `CLIENT_BRIEF_SYSTEM.md`).

### Phase 4 — Auto-attach portal metrics
**Files:** `webhook-server/portal_tickets.py`, `webhook-server/routes/portal_tickets.py`

Wire `campaign_review` fields 9/10/11/22 to the portal's own occupancy, ATR,
trend and budget-change data as a timestamped snapshot.

**Acceptance**
- A submitted review ticket carries occupancy, ATR, trend and budget-changed
  with an "as of" timestamp, without the requester typing them.
- Requester can override with a reason; the override is visible on the ticket.

### Phase 5 — Deflection + the return trip
1. Restore the KB search-before-submit step (`ticket-page-scope.md` §5).
2. Verify end-to-end: ClickUp Done → AI recap on the HubSpot company record →
   visible in the portal (`webhook-server/ticket_recap*.py`,
   `docs/clickup-ticket-recap-plan.md`).
3. Optional per §9.5: comment-from-portal → ClickUp comment.

---

## 8. Pre-flight check

Before building phase 1, verify the list IDs. If several `CLICKUP_LIST_*` env
vars point at the same list — or at a **view** ID rather than a list ID — that
compounds the field-pool problem and the manifest will resolve against the
wrong list.

```
GET /api/portal-tickets/admin/discover
```

Read-only; walks workspace `9011805260` → spaces → folders → lists and matches
by name/alias (`portal_tickets.discover_list_ids`, `config.py:167`). Confirm all
8 keys map to 8 **distinct** list IDs before proceeding.

---

## 9. Open decisions — defaults to build to

These were open in `ticket-page-scope.md` and are still open. Build to the
stated default; keep the alternative reachable by config so a reversal is a
setting, not a rewrite.

**9.1 Should `new_account_build` stay client-facing?**
45 fields including per-channel budget authorization and a signed-IO
prerequisite — that reads like Account Manager work.
→ **Default: make it internal**, alongside `dispo_cancel` and `new_business`.
Gate with `CLICKUP_NEW_ACCOUNT_AUDIENCE`, matching the existing
`CLICKUP_DISPO_AUDIENCE` pattern. Clients get 5 types.

**9.2 Does a brief update from a ticket apply immediately or get reviewed?**
→ **Default: immediate for descriptive fields** (amenities, hotspots, target
audience, colloquial terms, voice & tone), **reviewed for competitors and
forbidden phrases** — those two carry the most downstream risk in ad copy.

**9.3 Is the HubSpot Service Hub ticket path retired?**
`/api/ticket` → `ticket_manager.create_ticket` creates parallel HubSpot tickets
alongside ClickUp. That second system is what keeps this from being one loop.
→ **Default: retire it.** The recap note already covers the client-facing
record. Do this in phase 5, after ClickUp intake is proven.

**9.4 List IDs** — see §8. Resolve before phase 1.

**9.5 Can a requester comment on an open ticket from the portal?**
→ **Default: yes, in phase 5.** Most of today's re-asking happens in the
clarification round-trip, and it currently happens over email where it detaches
from the ticket.

---

## 10. Constraints

- **R1 (immutable):** code never writes `uuid`. Resolve first, always.
  See `IMMUTABLE_RULES.md`.
- **Layer rule:** the ticket app is Layer 3. Property identity resolves through
  the Property Resolver (Layer 2); brief writes go through
  `community_brief.py`, not direct HubSpot PATCHes.
- **Multi-tenancy:** everything `uuid`-scoped; no cross-client leakage.
- **Override-wins** for human-curated brief fields.
- Existing tests to keep green: `tests/test_portal_tickets.py`.
