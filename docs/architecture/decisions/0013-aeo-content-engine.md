# ADR 0013 — AEO Content Engine

**Status:** Accepted (skill stub committed this weekend; full build follow-on)
**Date:** 2026-05-16

## Context

AEO = Answer Engine Optimization — the equivalent of SEO for AI-native
search (ChatGPT, Perplexity, Claude, Gemini). Renters increasingly start
their apartment search by asking an LLM rather than typing into Google.
Properties that show up as answers are the ones that win.

Our `ai_mentions.py` module already MEASURES this: it scans which engines
mention each property and tracks a composite visibility index. What we
DON'T have today is the generation side — automatically producing
per-property content that answers the specific questions renters are
asking in those engines.

This belongs in the Engage stage of the Loop (ADR 0009).

## Decision

Build a per-property AEO Content Engine that closes the loop:
- Read the questions ChatGPT/Perplexity ARE getting about the property's
  submarket (from `ai_mentions` scans)
- Identify gaps — questions where our property page doesn't answer well
- Auto-generate structured FAQ + JSON-LD schema using Claude
- Publish to the HubSpot CMS property page partial
- Re-scan next week — did the property surface in answers?

### The skill: `webhook-server/aeo_writer.py`

```python
def generate_aeo_content(
    property_uuid: str,
    *,
    max_questions: int = 25,
    style: str = "warm-professional",
    fair_housing_check: bool = True,
) -> AEOContentDraft:
    """Generate a batch of Q&A blocks for one property.

    Inputs (pulled internally):
      - Property attributes from HubSpot (name, market, submarket, amenities)
      - AptIQ snapshot (real occupancy, pricing band, amenity list)
      - AI Mentions intent data (top questions in the property's market)
      - Existing AEO content (don't duplicate)

    Output:
      - List of Q&A blocks, each with: question text, answer, schema.org
        JSON-LD payload, source citations
      - Fair-housing safe (no age, family status, race, religion, etc.)
      - Anchored on AptIQ first-party data — no hallucinated amenities

    Returns AEOContentDraft (not yet published). Caller decides to publish
    or hold for human review (Premium tier auto-publishes; Standard tier
    routes through specialist approval).
    """
```

### Where the content lives

- HubDB table `rpm_aeo_content`:
  - `uuid` (property), `question_text`, `answer_md`, `json_ld`,
    `published_at`, `last_seen_in_engine_at`, `engines_citing` (array)
- HubSpot CMS partial `aeo-faq.html`:
  - Pulls rows from `rpm_aeo_content` filtered by uuid
  - Renders FAQ block + JSON-LD `<script>` tag for answer engines

### Closed-loop measurement

After `aeo_writer.generate_aeo_content` publishes content:
1. Loop event: `stage='engage', event_type='aeo_content_published',
   property_uuid, payload={question_count, batch_id}`
2. Next week's AI Mentions scan checks if the property is now cited for
   any of the published questions
3. If cited: `loop_event(stage='engage', event_type='aeo_citation_detected',
   payload={question, engine, batch_id})` — credits the AEO content
4. Optimize stage reads these to decide which content patterns drive
   AI citations and feeds that learning into the next generation cycle

### Tier gating

- **Local / Lite**: not available
- **Basic**: not available (out of scope per 2026 SEO Package Strategy)
- **Standard**: available with human-approval flow (writes drafts; AM
  reviews before publish)
- **Premium**: available with auto-publish + post-publish review

Read tier from HubSpot company `seo_tier` property at every invocation.

### Fair housing

`fair_housing.py` already provides language safety checks. AEO content
goes through it before publish. The Claude prompt also explicitly forbids:
- Age (no "perfect for young professionals")
- Family status (no "great for couples")
- Race, ethnicity, religion, national origin
- Disability
- Schools as code for demographic targeting

Detection of any of these in the draft = automatic reject + flag for
human rewrite.

### Volume bounds

To avoid Google "doorway pages" penalties:
- Max 25 new Q&A blocks per property per month
- Each block must answer a unique high-intent question
- Total AEO content cap: 200 Q&As per property lifetime (then we recycle)

## Consequences

**Trade-offs accepted:**
- Adds dependency on Claude API for generation (cost: ~$0.05 per property
  per month for 25 questions)
- Adds publishing complexity (HubDB writes + CMS template changes)
- Requires fair-housing review on every batch

**What we gain:**
- First-mover on AEO for multifamily (most operators don't generate
  structured FAQ content this way)
- Closed measurement loop — we can prove which content drives citations
- Standard/Premium tier differentiator
- The content compounds over time per property

**This weekend ships:**
- Skill stub `aeo_writer.py` with the function signature
- HubDB migration for `rpm_aeo_content` table
- Placeholder Loop events: `aeo_content_published`, `aeo_citation_detected`

**Follow-on work (next sprint):**
- Full Claude prompt design + test on 3 properties
- CMS partial `aeo-faq.html` rendering
- Wire up the Engage event chain

## References

- ADR 0009 — Multifamily Loop (where AEO sits in Engage)
- `webhook-server/ai_mentions.py` (the measurement counterpart)
- `webhook-server/fair_housing.py` (safety filter)
- `webhook-server/aeo_writer.py` (this weekend's stub)
