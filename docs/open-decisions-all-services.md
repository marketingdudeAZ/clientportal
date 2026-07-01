# Open Decisions & Questions — All Marketing Services → Portal + HubSpot

Parking lot for the "add Creative / Branding / Reputation / Social to the portal,
deals & quotes" work. Come back to these; each has an owner and (where relevant)
the options on the table. Nothing here is decided until moved to a decision log.

_Last updated: 2026-07-01 (session with Claude). Context: catalog handoff, Branding
SOW, Programs Guide, and the Creative Services Cost Sheet (ClickUp export) are in hand._

---

## A. Change-order / overage model  *(newest — from the 1-deal / 2-quote decision)*

Decision so far: **one deal → two quotes.** Quote 1 = Kickoff (signed to start),
Quote 2 = Change Order (overage, calculated at project end). Applies to **Branding
and CSR** (Creative Service Requests). HubSpot supports 1 deal → many quotes natively.

Open questions:
1. **Overage unit** — bill by **hours** (log actual revision hours; the cost sheet's
   `Design Hours` per SKU is the estimate baseline) vs a flat per-extra-round fee?
   *(Leaning hours-based.)* — Kyle
2. **Who logs the hours** — designer/PM via ClickUp time tracking on the revision
   task, feeding Q2? Confirm the mechanism. — Kyle + Creative/Katrina
3. **Overage rate** — $90/hr (Branding SOW) vs $95/hr (Creative rate card): which
   pipeline uses which? — Katrina
4. **Q1 basis** — fixed "Starts at" price per deliverable (simple, recommended) vs
   hours × rate. Keep hours only for the Q2 overage? — Kyle
5. **Q2 approval** — does the client re-sign the change order, or is it billed
   automatically under the SOW terms already agreed ($/hr overage)? — Kyle + Legal?
6. **Pipeline stages** — confirm: Request → Kickoff Quote (Q1) Sent → Signed → In
   Production/Revisions → Overage Review → *(if overage)* Change Order (Q2) →
   Approved → Delivered/Closed-Won. No-overage jobs skip Q2. — Kyle + HubSpot admin
7. **Deal amount** = Q1 + Q2(final) — confirm how/when the deal amount updates. — HubSpot admin

---

## B. Motion model & pipelines

8. **Confirm the motion model** — services grouped by sales motion (A Subscription /
   B Project-SOW / C À-la-carte), and Social Posting + Reputation + SOCi join the
   **Digital** pipeline (same motion, reuse existing checkout). — Kyle + Katrina
9. **Approve two new pipelines** — Branding and Creative — with stages + deal
   properties. — HubSpot admin
10. **Creative pipeline vs order queue** — does Creative need a full pipeline, or a
    lighter "order → fulfillment queue" with a billing deal? — Kyle

---

## C. Non-elective automation

11. **What does "Non-Elective" mean** (Reputation, SOCi) — auto-**billed** (required)
    or auto-**recommended** (AM confirms)? — Katrina + Kyle
12. **Applicability rule** — which properties auto-attach non-elective services?
    (All RPM-Managed? by market? by lifecycle stage?) — Katrina + Kyle

---

## D. Catalog & pricing

13. **Canonical price per service** — SOW vs Programs Guide vs Creative rate card
    disagree (Floor Plan Sheet, Social Templates, Display Ads, overage rate,
    Branding "$6,500+" vs $8k/$6k/$3.5k). Pick one source per category. — Katrina (+ Andrew)
14. **Branding "$6,500+"** — legacy price, floor, bundle, or deprecate? — Katrina
15. **GL code mapping** (Yardi + OneSite) for ALL SKUs. — Katrina / Accounting
16. **Reconcile Creative cost sheet vs old rate card** deltas before ingestion. — Andrew
17. **Catalog home** — HubDB (metadata) + HubSpot Products (billing objects), mapped
    `sku_id ↔ hs_product_id`. Confirm. — Kyle + HubSpot admin
18. **Catalog owner** — who maintains HubDB (pricing changes, new SKUs)? — Marketing
19. **"Custom quote" SKUs** — Window Wraps, Other Signage, Website Consulting
    ($95/hr, 3-hr min): always estimate/change-order, never fixed-price checkout.
    Confirm handling. — Katrina

---

## E. HubSpot setup

20. **Create HubSpot Products** for every sellable SKU lacking one — Reputation
    (no product id yet), Website Hosting (no product at all), all Branding/Creative
    SKUs. — HubSpot admin
21. **SOW + e-signature** for Branding — Quotes-native e-sign vs DocuSign; SOW legal
    terms (90-day validity, 2 rounds, overage rate) live on the quote/SOW template. — Kyle + Legal
22. **Quote templates per motion** — the digital IO template won't fit a Branding SOW
    or a Creative order. — HubSpot admin
23. **Mixed-cadence quotes** — a property may carry monthly (SEO/Social) + one-time
    (a brochure) + per-brand (Branding) on one deal. Confirm HubSpot handles recurring
    + one-time in one quote, or split. — HubSpot admin
24. **Setup fees** as separate one-time line items (Social $500, Reputation $50). — HubSpot admin
25. **Pop!Shop** fulfillment wrinkle for Business Cards (service fee included). — Creative

---

## F. Portal / UX

26. **Catalog-driven Services surface** with 3 flows (Digital checkout / Branding
    request / Creative cart). — Kyle
27. **Lifecycle → collateral variant** filtering (New Dev / Lease-Up vs Stable vs
    Expansion see different collateral). Store lifecycle on the company. — Kyle
28. **Dependencies** (e.g., GEO requires SEO Basic+) drive selectability. — Kyle
29. **Pricing visibility** — internal vs client views (Branding is $3.5–8k) via the
    Beta/Prod access layer already built. — Kyle
30. **Approval routing** — self-serve for cheap recurring; AM approval for
    Branding/large before the deal advances. — Kyle

---

## G. Launch scope & owners

31. **Launch scope** — ship **Motion A** (Digital + Social/Rep/SOCi) now; do NOT gate
    it on Branding/Creative. Confirm. — Kyle + Katrina
32. **Still-needed data** — full ClickUp catalog (Creative ✅ in hand; Branding SOW ✅;
    remaining categories?), revision/overage rates per SKU (Andrew), GL codes (Katrina).
33. **Owners & dates** — assign for: ClickUp export gaps, canonical pricing, GL codes,
    overage rates, HubSpot pipelines/products, SOW/e-sign, portal build.

---

### What's already built (so we don't re-decide it)
- `product_catalog.py`: 18 channels → HubSpot product ids; 13 auto-include on every
  quote; tier-aware resolution (Social Posting Basic/Standard/Premium).
- Management fee: 20% of paid spend, $250 floor (real formula, not a stub).
- Setup fees: Social $500, Reputation $50.
- `routes/self_checkout.py`, `deal_creator.py`, `quote_generator.py`: digital deal +
  line-item + quote automation.
- ClickUp → HubSpot company-notes loop (PR #20) — fulfillment progress back on the account.
