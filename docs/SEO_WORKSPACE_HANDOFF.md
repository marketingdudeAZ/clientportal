# RPM Living SEO Workspace — Handoff for the SEO Team

*A practical overview of the rebuilt SEO packages and the client-facing workspace we built for you and your team to execute in.*

---

## TL;DR

We rebuilt RPM's SEO packaging around what actually moves the needle for apartment communities in 2026: **a hybrid of traditional Google rankings and Generative Engine Optimization (GEO)** — the emerging discipline of getting properties cited in ChatGPT, Perplexity, Gemini, and Google's AI Overviews.

Alongside that, we built a **client portal workspace** that gives each property on an SEO retainer a live dashboard and gives your team a unified place to track rankings, scan AI citations, plan content, and research keywords — all keyed to the right client automatically.

The intent is simple: **remove the ambiguity from "what am I paying for?"** (for clients) and **remove the monthly reporting grind** (for your team), so the team spends time on strategy and execution instead of Excel.

---

## 1. Why the old packaging needed a rebuild

The previous SEO tiers (Local / Lite / Basic / Standard / Premium) were priced but the **deliverables weren't clearly tied to the price point**. Clients would ask "what's the difference between Standard and Premium?" and the answer was vague — more of the same work, maybe a quarterly report.

We kept the five tier names (they're familiar to the sales team) but **redefined what unlocks at each level**, tied each feature to a concrete deliverable the client sees in their portal, and built the tooling so your team can deliver consistently at scale.

## 2. The new package structure

| Tier | Monthly | What's included |
|------|---------|-----------------|
| **Local** | $100 | Citation management + tracked keywords table (visible to client, positions update daily) |
| **Lite** | $300 | Local + off-page authority (directory cleanup, citation consistency) |
| **Basic** | $500 | Lite + **AI Mentions tracking** + **keyword research self-serve** |
| **Standard** | $800 | Basic + **content clusters** + **AI-generated content briefs** + **trend exploration** |
| **Premium** | $1,300 | Standard + **content decay monitoring** + full ILS schema sync + priority support |

Every tier gets access to the portal. What changes is **which tabs light up** inside it.

### What each tier unlocks in the client's portal

```
             Local   Lite   Basic   Standard   Premium
──────────────────────────────────────────────────────
Rankings      ✓      ✓      ✓       ✓          ✓
Competitors   ✓      ✓      ✓       ✓          ✓
AI Mentions   ·      ·      ✓       ✓          ✓
Keyword Research                    ✓          ✓          ✓
Content Clusters                    ✓          ✓
Content Briefs                      ✓          ✓
Trend Explorer                      ✓          ✓
Content Decay                                  ✓
```

This gives sales a clean upgrade story: *"You're on Basic — upgrading to Standard gets you the full Content Planner with AI-generated briefs and trend analysis."*

---

## 3. The workspace we built for your team

Every RPM property with any SEO tier gets a **SEO & AI Insights** section in their client portal. Access is automatic based on their active SEO package — no manual provisioning.

### Three main workspaces

#### a) SEO & AI Insights (all tiers)
The home dashboard. Shows:
- **KPI row** — tracked keywords count, top-3 positions, improving-30d count, AI visibility index
- **Visibility Trend** — 90-day line chart of overall ranking health
- **Tracked Keywords table** — every keyword you're tracking for the property, current position, 7-day delta, 30-day delta, target position, ranked URL
- **AI Mentions** (Basic+ tier) — weekly scan of how often the property is cited across ChatGPT, Perplexity, Gemini, and Google AI Overview for common apartment-hunting prompts
- **Competitors** — domains the property competes against for tracked keywords

Your team adds keywords and competitors through the portal UI (there's an "Add keyword" button visible to anyone with Basic+ tier). Data refreshes automatically — daily for rankings, weekly for AI mentions.

#### b) Content Planner (Standard+ tier)
The operational workspace for content strategy:
- **Topic Clusters** — tracked keywords automatically grouped into hub-and-spoke topics based on SERP overlap (if "apartments winter garden fl" and "luxury apartments winter garden" share 4+ ranking URLs, they cluster together)
- **Content Briefs** — one-click AI-generated brief per cluster: H1, meta description, outline with H2s/H3s, target entities to include, People-Also-Ask questions to answer, suggested schema.org types, and internal-link plan
- **Refresh Queue** (Premium only) — list of URLs whose rankings have dropped 5+ positions over the last 30 days, prioritized for content refresh

The brief generator uses **Claude Haiku** and costs ~$0.01 per brief. Your team reviews, approves, and the brief auto-routes to the Content team via a ClickUp task in the SEO list.

#### c) Research tools (Basic+ tier for research, Standard+ for trends)
Self-serve discovery for both your team and the client's AM:
- **Ideas** — seed-to-cluster keyword expansion with volume, difficulty, intent, CPC, SERP features. Multi-select → bulk "Add to tracking" for anything worth ranking for
- **Difficulty** — paste a list, get color-coded difficulty scores (green <30, amber 30–60, red 60+)
- **Competitor Gap** — enter any competitor's domain, get the keywords they rank for that the property doesn't, filtered to low-difficulty opportunities
- **Trends** (Standard+) — Google Trends integration showing 12-month or 5-year search interest for up to 5 keywords at once, plus seasonal peak detection ("peak month for 'apartments winter garden fl' is March")

---

## 4. The methodology behind the tooling

We built this around **Michael King's iPullRank framework** for technical SEO + content, layered with **Generative Engine Optimization** principles for AI-era discovery.

The key insight driving the design: **the tactics that win organic rankings in Google are the same tactics that get you cited in ChatGPT** — entity coverage, semantic depth, direct answers to common questions, schema markup, and content freshness. So we don't treat "SEO" and "AI citation" as separate workstreams. Every feature in the workspace optimizes for both simultaneously.

Concrete examples of how the methodology shows up in the tool:

| Principle | Where it shows up |
|-----------|-------------------|
| Hub-and-spoke topic clustering | Content Planner → Topic Clusters tab |
| Entity SEO (Google Knowledge Graph) | Brief generator includes "target_entities" per H2 |
| Query fan-out (People-Also-Ask coverage) | Briefs explicitly list PAA questions to answer in early sections |
| Semantic gap analysis | Competitor Gap tool + Content Planner semantic_gaps() |
| Schema.org optimization | Brief generator recommends `ApartmentComplex`, `FAQPage`, `BreadcrumbList` when missing |
| Content decay monitoring | Refresh Queue flags URLs losing rank |
| Internal linking | Brief outputs `internal_link_targets` chosen from existing tracked URLs |
| AI citation monitoring | Weekly AI Mentions scan across 4 major LLMs |

Your team doesn't need to memorize any of this — the tool surfaces the right next action. The framework is baked in.

---

## 5. What a week in the workspace looks like for your team

### Monday morning (automatic — runs while you sleep)
The Monday cron job fires at 5 AM CT and:
- Rescans SERP positions for every tracked keyword across the portfolio
- Runs AI Mentions scans (ChatGPT, Perplexity, Gemini, AIO)
- Runs on-page crawl per property → updates health score
- Rebuilds topic clusters (Standard+ properties)
- Populates the Refresh Queue from BigQuery rank history (Premium properties)

When your team logs in Monday, **every client's dashboard is current**. No pull, no generate-report, no Excel.

### Tuesday — content planning
Open the Content tab for each Standard+ client. Review the clusters. Generate briefs for the top 1-3 clusters per client per month. Approve → briefs route automatically to the Content team's ClickUp queue.

### Wednesday/Thursday — execution
Your team responds to flagged items (e.g., Refresh Queue alerts for Premium clients), follows up on brief approvals, tunes keyword lists based on the Research tab's competitor gap findings.

### Friday — strategy time
The data is already there; your team spends Friday on higher-leverage work instead of reporting. Quarterly deep-dives with clients pull from the same portal — clients literally see what you see.

### Onboarding a new client (10-minute workflow)
1. Populate 20–30 tracked keywords via the portal's Add Keyword UI, or paste into a HubDB CSV
2. Add 3–5 competitor domains
3. Run a one-click manual refresh (via internal admin endpoint) to populate initial data so the client's first portal view looks complete on day one
4. The client's next login shows rankings, AI citations, competitor gaps, and (for Standard+) auto-generated topic clusters within 90 seconds

---

## 6. What your team doesn't need to do anymore

- ❌ Build monthly rank-tracking PowerPoints — the portal *is* the report
- ❌ Manually run SEMrush/Ahrefs exports for each client — it's baked in via DataForSEO
- ❌ Write briefs from scratch for every new page — draft generation is one click
- ❌ Monitor for decaying content by hand — the queue flags it
- ❌ Track AI citations manually — the weekly scan does it

Your team's job shifts from **data assembly** to **strategy, approvals, and client conversations**.

---

## 7. Data sources + reliability

Everything in the workspace is powered by real live data:

| Signal | Source |
|--------|--------|
| Keyword rankings | **DataForSEO** live SERP API — Google's actual results, not estimates |
| AI Mentions | **DataForSEO LLM endpoints** — live prompts against ChatGPT, Perplexity, Gemini, Google AI Overview |
| Keyword ideas + difficulty | **DataForSEO Labs** — same data as premium SEO tools |
| Competitor gap | **DataForSEO domain intersection** |
| Trends | **Google Trends** via DataForSEO |
| Content briefs | **Claude Haiku 4.5** (Anthropic) — combines the SERP + competitor data above into the brief |
| On-page audit | **DataForSEO on-page crawler** |
| Historical rank trends | Archived in **Google BigQuery** (rolling 90-day window in-portal, 2-year retention for compliance) |

Cost to RPM per property per month, at Standard tier:
- DataForSEO: ~$2/mo (30 keywords daily + weekly AI scan + monthly crawl)
- Claude briefs: ~$0.04/mo (assume 4 briefs/mo)
- **Total delivery cost per property: ~$2.05**

That's a healthy margin on a $800/mo Standard package, and the cost is the same at Premium ($1,300) — meaning the upgrade path is pure margin.

---

## 8. Pilot status

We've launched the workspace with **Muse at Winter Garden** as the pilot property (Standard tier). First cron runs Monday; manual refresh already triggered so the SEO team can walk through a live example today.

Over the next two weeks we'll:
1. Expand the property allowlist from 1 → all active SEO retainers
2. Train the SEO team on the Content Planner workflow (90-min session)
3. Run the first round of briefs through approval + handoff to Content
4. Start quarterly reviews against the data

---

## 9. Questions your SEO manager will likely ask

**"Does this replace SEMrush / Ahrefs / BrightLocal?"**
For day-to-day rank tracking, keyword research, and competitor analysis — yes. For deep backlink analysis and historical audits, keep Ahrefs for now. We can evaluate replacing it once we have 3–6 months of BigQuery history.

**"Who writes the briefs — Claude or humans?"**
Claude generates the *draft* (H1, outline, entity targets, PAA answers, schema). Your team reviews and approves. Content team writes the actual copy against the brief. Claude is a research and skeletoning tool, not a copywriter.

**"What if the AI Mentions data is wrong?"**
The scans are deterministic (same prompts every week), so we're measuring *relative* trends rather than absolute truth. If the composite AI visibility index for a property climbs from 40 → 65 over 3 months, that's real. If a single scan has a weird anomaly, the 4-week trend smooths it out.

**"How do clients react to seeing their rankings in real-time?"**
Generally positively — it replaces the "what's happening with our SEO?" monthly question with a live dashboard. The places to be careful: clients see decline data too. Your team should set the tone proactively (e.g., "Here's the refresh queue, here's what we're doing about it") rather than have clients discover it cold.

**"Can we export data to share with property management?"**
Not yet via UI. For now, the team can query BigQuery directly or screenshot the portal. Add to the Q3 roadmap if needed.

---

## 10. Where to find it

- **Live portal URL (test property):** https://digital.rpmliving.com/portal-dashboard?uuid=10559996814 (Muse at Winter Garden)
- **Team access:** any team member with HubSpot membership login
- **Codebase:** `https://github.com/marketingdudeAZ/clientportal`
- **Main contact for tooling issues:** Kyle Shipp

---

*Prepared for the RPM SEO team — April 2026*
