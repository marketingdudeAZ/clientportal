# HubSpot Workflow Spec — Gap Review Email Automation

**Audience:** HubSpot admin who will build the workflow.
**Goal:** When the Flask portal flags an intake form as having gaps, HubSpot
auto-creates a task on the company's owner with a pre-drafted email to send
to the Community Manager. If no response comes back, HubSpot escalates.

The Flask app does **not** call the HubSpot tasks API. Instead, it sets a
property on the company (`rpm_gap_review_action`) and HubSpot's workflow
engine watches for the change and creates the task. This avoids needing the
`tasks` scope on our private app token.

---

## Inputs the Flask app provides

When a gap is detected on an intake submission, the app updates these
company properties (all created by `scripts/create_onboarding_properties.py`):

| Property | Set to | Notes |
|---|---|---|
| `rpm_gap_review_action` | `send_cm_email` | Trigger value the workflow watches for |
| `rpm_gap_review_token` | `<random 32-char token>` | Used in the response form URL |
| `rpm_gap_review_questions` | `<JSON array>` | Questions to include in the email |
| `rpm_gap_review_status` | `sent` *(after workflow fires)* | Updated by the workflow's last action |
| `community_manager_email` | *(already populated from intake)* | Email recipient |
| `community_manager_name` | *(already populated from intake)* | Personalization |
| `regional_manager_email` | *(already populated from intake)* | Escalation recipient |

Same pattern for `send_rm_email` and `escalate` actions.

---

## Workflow 1 — Send CM Email (initial trigger)

**Type:** Company-based workflow.

**Enrollment trigger:**
- `rpm_gap_review_action` *is equal to* `send_cm_email`
- AND `rpm_gap_review_status` *is none of* `sent`, `responded`

**Re-enrollment:** Allow re-enrollment when `rpm_gap_review_action` changes.
(Lets the same company go through the loop again on a subsequent intake.)

### Actions in order

1. **Create task**
   - Assigned to: company owner
   - Type: Email
   - Subject: `Send onboarding gap review to {{ community_manager_name }}`
   - Due date: 1 business day from now
   - Body:
     ```
     A few onboarding details for {{ company.name }} need verification from
     the Community Manager. The email below is pre-drafted — review, edit
     if needed, then send it from this task to log it on the company.

     ─── Email to send ───
     To: {{ company.community_manager_email }}
     Subject: Quick onboarding details for {{ company.name }}

     Hi {{ company.community_manager_name }},

     We're getting {{ company.name }}'s marketing set up at RPM and need
     about 5 minutes of your input to fill a few gaps. The form is
     pre-filled with what we already have — just review and correct.

     → Respond here:
     https://digital.rpmliving.com/client-portal/onboarding/gap-response/{{ company.rpm_gap_review_token }}

     Thanks,
     {{ owner.firstname }}
     ```

2. **Set property**
   - `rpm_gap_review_status` → `sent`
   - `rpm_gap_review_email_sent_at` → *(current datetime)*

3. **Set property** (reset trigger so workflow doesn't re-fire)
   - `rpm_gap_review_action` → `none`

---

## Workflow 2 — CM No-Response Escalation

**Type:** Company-based workflow.

**Enrollment trigger:**
- `rpm_gap_review_status` *is equal to* `sent`
- AND `rpm_gap_review_email_sent_at` *is more than* 48 hours ago
- AND `rpm_gap_review_response_at` *is unknown*

### Actions in order

1. **Create task** — assigned to company owner
   - Subject: `Escalate gap review to RM — {{ company.name }}`
   - Body:
     ```
     The Community Manager hasn't responded to the gap-review email after
     48 hours. Send the same questions to the Regional Manager.

     To: {{ company.regional_manager_email }}
     Subject: Onboarding details for {{ company.name }} — needs RM input

     Hi {{ company.regional_manager_name }},

     We sent {{ company.community_manager_name }} a quick onboarding form
     two days ago and haven't heard back. Could you either nudge them or
     fill it in directly?

     → https://digital.rpmliving.com/client-portal/onboarding/gap-response/{{ company.rpm_gap_review_token }}

     Thanks,
     {{ owner.firstname }}
     ```

2. **Set property**
   - `rpm_gap_review_status` → `escalated`

---

## Workflow 3 — Final Timeout (flag deal as escalated)

**Type:** Company-based workflow.

**Enrollment trigger:**
- `rpm_gap_review_status` *is equal to* `escalated`
- AND `rpm_gap_review_email_sent_at` *is more than* 72 hours ago
- AND `rpm_gap_review_response_at` *is unknown*

### Actions in order

1. **Set property**
   - `rpm_onboarding_status` → `escalated`
   - `rpm_onboarding_status_changed_at` → *(current datetime)*

2. **Create task** — assigned to company owner
   - Subject: `Onboarding stalled — {{ company.name }} needs Director attention`
   - Priority: High
   - Body: Brief summary explaining no CM/RM response received; manual
     intervention required to keep the 5-7 day SLA on track.

---

## Workflow 4 — Response Received (close the loop)

**Type:** Company-based workflow.

**Enrollment trigger:**
- `rpm_gap_review_response_at` *is known* AND *changes*

The Flask app sets `rpm_gap_review_response_at` when the CM submits the
portal response form. This workflow just resets the status.

### Actions

1. **Set property** — `rpm_gap_review_status` → `responded`
2. **Mark related tasks complete** — close out workflows 1 + 2 tasks

---

## SLA Breach Workflow (separate, watches the whole pipeline)

**Type:** Company-based workflow.

**Enrollment trigger:**
- `rpm_onboarding_status_changed_at` *is more than* X hours ago
- WHERE X depends on the current `rpm_onboarding_status` (see
  `ONBOARDING_SLA_PER_STAGE_HOURS` in `config.py`)

This is best built as separate workflows per stage — HubSpot's UI doesn't
support conditional thresholds in a single trigger. Recommend one workflow
per stage that has a tight SLA:

| Stage | SLA | Workflow needed |
|---|---|---|
| `intake_sent` | 48 h | yes |
| `intake_in_progress` | 24 h | yes |
| `brief_drafting` | 6 h | yes |
| `brief_review` | 24 h | yes |
| `strategy_in_build` | 72 h | yes |
| `awaiting_client_approval` | 24 h | yes |

Each one creates a "stage stalled" task on the company owner when the
threshold trips.

---

## Test plan (HubSpot admin)

Before enabling on production:

1. Create a test company with `community_manager_email` set.
2. Manually set `rpm_gap_review_action = send_cm_email` and
   `rpm_gap_review_token = test-token-1234`.
3. Confirm Workflow 1 fires within 1 minute and creates a task on the
   company owner with the email body rendered.
4. Manually set `rpm_gap_review_email_sent_at` to 49 hours ago.
5. Confirm Workflow 2 fires.
6. Reset and verify the response-received path by setting
   `rpm_gap_review_response_at` to current time.

---

## What the Flask app owns vs. what HubSpot owns

| Concern | Owner |
|---|---|
| Detecting gaps on intake (slop classifier, completeness, typos) | Flask |
| Setting `rpm_gap_review_action` and `_token` properties | Flask |
| Hosting the response form at `/onboarding/gap-response/<token>` | Flask |
| Setting `rpm_gap_review_response_at` on form submit | Flask |
| Creating owner tasks | HubSpot Workflow |
| Reminder/escalation timing | HubSpot Workflow |
| Email body templates | HubSpot Workflow (editable without code change) |
| Marking tasks complete on response | HubSpot Workflow |

This split keeps the email-template editing in the hands of the marketing
admins (no code deploy to change wording) and removes the need for the
`tasks` scope on our private app token.
