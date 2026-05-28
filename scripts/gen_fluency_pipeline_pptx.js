/* Generates a slide-by-slide briefing deck of the Fluency Tag Pipeline —
 * "How Property Data Gets Pulled In." RPM brand palette, numbered-step motif.
 * Run: node scripts/gen_fluency_pipeline_pptx.js
 * Output: ~/Downloads/RPM_Property_Tag_Pipeline.pptx
 */
const pptxgen = require(require("child_process").execSync("npm root -g").toString().trim() + "/pptxgenjs");
const os = require("os");
const path = require("path");

const NAVY = "1A2530", NAVY2 = "243240", COPPER = "C8964E", COPPER_LT = "E6C68A";
const LIGHT = "F0F2F5", WHITE = "FFFFFF", SAGE = "8FA68E", INK = "1F2937", MUTE = "6B7280";
const CARD = "FFFFFF", CARDLINE = "D9DEE5";
const HF = "Georgia", BF = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";          // 13.33 x 7.5
pres.author = "Kyle Shipp";
pres.title = "RPM Property Tag Pipeline";
const W = 13.33, H = 7.5;

const sh = () => ({ type: "outer", color: "000000", blur: 7, offset: 3, angle: 135, opacity: 0.13 });

// ── Footer applied to content slides ─────────────────────────────────────────
function footer(slide, n) {
  slide.addText("RPM Property Tag Pipeline  ·  How property data gets pulled in",
    { x: 0.6, y: H - 0.42, w: 9, h: 0.3, fontFace: BF, fontSize: 9, color: MUTE, align: "left", margin: 0 });
  slide.addText(String(n), { x: W - 1.0, y: H - 0.42, w: 0.5, h: 0.3,
    fontFace: BF, fontSize: 9, color: MUTE, align: "right", margin: 0 });
}

// ── Step slide: numbered circle + title + what-happens, card on the right ─────
function stepSlide(n, kicker, title, whatLines, cardTitle, cardRows, cardAccent) {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  // left rail
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });

  // numbered circle
  s.addShape(pres.shapes.OVAL, { x: 0.7, y: 0.7, w: 1.15, h: 1.15, fill: { color: NAVY }, shadow: sh() });
  s.addText(String(n), { x: 0.7, y: 0.7, w: 1.15, h: 1.15, align: "center", valign: "middle",
    fontFace: HF, fontSize: 40, bold: true, color: COPPER_LT, margin: 0 });

  // kicker + title
  s.addText(kicker.toUpperCase(), { x: 2.1, y: 0.72, w: 6.7, h: 0.35, fontFace: BF, fontSize: 13,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 2.1, y: 1.06, w: 6.9, h: 0.95, fontFace: HF, fontSize: 28, bold: true,
    color: NAVY, margin: 0 });

  // what-happens body
  s.addText(whatLines.map((t, i) => ({ text: t, options: { bullet: { code: "2022", indent: 14 },
    breakLine: true, paraSpaceAfter: 9 } })),
    { x: 2.15, y: 2.25, w: 5.0, h: 4.4, fontFace: BF, fontSize: 15, color: INK, valign: "top" });

  // right card
  const cardX = 7.7, cardY = 1.9, cardW = 5.0, cardH = 4.6;
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: cardH,
    fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: 0.62,
    fill: { color: cardAccent || NAVY2 } });
  s.addText(cardTitle, { x: cardX + 0.25, y: cardY, w: cardW - 0.5, h: 0.62, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText(cardRows.map((r) => ({ text: r, options: { bullet: false, breakLine: true,
    paraSpaceAfter: 7 } })),
    { x: cardX + 0.3, y: cardY + 0.85, w: cardW - 0.6, h: cardH - 1.1, fontFace: "Consolas",
      fontSize: 12, color: INK, valign: "middle" });

  footer(s, n + 3);   // step slides are deck positions 4-13
  return s;
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 1 — Title (dark)
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.28, fill: { color: COPPER } });
  s.addText("RPM LIVING  ·  DIGITAL PRODUCTS & SERVICES", { x: 0.9, y: 1.5, w: 11, h: 0.4,
    fontFace: BF, fontSize: 14, bold: true, color: COPPER, charSpacing: 3, margin: 0 });
  s.addText("How Property Data Gets Pulled In", { x: 0.9, y: 2.1, w: 11.5, h: 1.5,
    fontFace: HF, fontSize: 52, bold: true, color: WHITE, margin: 0 });
  s.addText("The RPM Property Tag Pipeline — source to ad, step by step",
    { x: 0.9, y: 3.7, w: 11, h: 0.7, fontFace: BF, fontSize: 22, color: COPPER_LT, margin: 0 });
  s.addText("Team briefing  ·  Digital + Property Marketing", { x: 0.9, y: 6.4, w: 11, h: 0.4,
    fontFace: BF, fontSize: 13, color: "9AA7B4", margin: 0 });
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 2 — The big idea (light)
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addText("THE BIG IDEA", { x: 0.7, y: 0.7, w: 11, h: 0.4, fontFace: BF, fontSize: 14,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText("Every property carries a set of marketing tags. We derive them automatically, every day.",
    { x: 0.7, y: 1.15, w: 12, h: 1.3, fontFace: HF, fontSize: 28, bold: true, color: NAVY, margin: 0 });

  const cards = [
    ["WHAT IT IS", "A daily pipeline that turns property facts into marketing tags — voice, amenities, neighborhood, competitors, lifecycle.", SAGE],
    ["WHAT IT POWERS", "The /accounts/property dashboard your team reads, AND the paid-media copy + targeting Fluency generates.", COPPER],
    ["WHO STEERS IT", "Property Marketing can override any auto-derived field through the Community Brief — and their edit wins.", NAVY2],
  ];
  cards.forEach((c, i) => {
    const x = 0.7 + i * 4.1;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 2.9, w: 3.8, h: 3.3, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 2.9, w: 3.8, h: 0.12, fill: { color: c[2] } });
    s.addText(c[0], { x: x + 0.3, y: 3.2, w: 3.2, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
      color: c[2], charSpacing: 1, margin: 0 });
    s.addText(c[1], { x: x + 0.3, y: 3.7, w: 3.2, h: 2.3, fontFace: BF, fontSize: 15, color: INK,
      valign: "top", margin: 0 });
  });
  footer(s, 2);
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 3 — End-to-end flow (light)
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addText("THE WHOLE PICTURE, ONE GLANCE", { x: 0.7, y: 0.55, w: 11, h: 0.4, fontFace: BF,
    fontSize: 14, bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText("Three sources → one merge → two writes → Fluency", { x: 0.7, y: 0.95, w: 12, h: 0.7,
    fontFace: HF, fontSize: 26, bold: true, color: NAVY, margin: 0 });

  // 3 source boxes (left column)
  const srcs = [
    ["ApartmentIQ CSV", "amenities · floor plans · year · rent · occupancy"],
    ["Property website", "unit noun · neighborhood · landmarks · employers"],
    ["ClickUp forms", "must-include · forbidden phrases (phase 2.3)"],
  ];
  srcs.forEach((src, i) => {
    const y = 2.0 + i * 1.35;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 3.5, h: 1.1, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 0.1, h: 1.1, fill: { color: SAGE } });
    s.addText(src[0], { x: 0.95, y: y + 0.12, w: 3.1, h: 0.4, fontFace: BF, fontSize: 15, bold: true,
      color: NAVY, margin: 0 });
    s.addText(src[1], { x: 0.95, y: y + 0.5, w: 3.1, h: 0.55, fontFace: BF, fontSize: 11, color: MUTE,
      margin: 0, valign: "top" });
  });

  // manifold: vertical bus collecting all 3 source boxes (right edges sit at x=4.2)
  s.addShape(pres.shapes.LINE, { x: 4.2, y: 2.55, w: 0, h: 2.7, line: { color: COPPER, width: 2.5 } });
  // arrow from bus → merge box (bus midpoint y=3.9 = merge center)
  s.addShape(pres.shapes.LINE, { x: 4.2, y: 3.9, w: 1.2, h: 0, line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });

  // merge box (center, vertically centered on y=3.9 to align both columns)
  s.addShape(pres.shapes.RECTANGLE, { x: 5.4, y: 2.9, w: 2.7, h: 2.0, fill: { color: NAVY }, shadow: sh() });
  s.addText("tag_builder", { x: 5.4, y: 3.1, w: 2.7, h: 0.4, align: "center", fontFace: "Consolas",
    fontSize: 14, bold: true, color: COPPER_LT, margin: 0 });
  s.addText("MERGE ENGINE", { x: 5.4, y: 3.5, w: 2.7, h: 0.35, align: "center", fontFace: BF,
    fontSize: 11, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText("facts → tags\noverride wins", { x: 5.4, y: 3.9, w: 2.7, h: 0.85, align: "center",
    fontFace: BF, fontSize: 12, color: "9AA7B4", margin: 0 });

  // arrow from merge → write column, then a bus distributing to both write boxes
  s.addShape(pres.shapes.LINE, { x: 8.1, y: 3.9, w: 1.1, h: 0, line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
  s.addShape(pres.shapes.LINE, { x: 9.2, y: 3.075, w: 0, h: 1.5, line: { color: COPPER, width: 2.5 } });

  // 2 write boxes (right column)
  const writes = [
    ["HubSpot fluency_*", "full set incl. pricing\npowers /accounts/property", COPPER],
    ["Fluency Google Sheet", "marketing-safe subset\nkeyed by uuid → Fluency reads it", SAGE],
  ];
  writes.forEach((wr, i) => {
    const y = 2.45 + i * 1.5;
    s.addShape(pres.shapes.RECTANGLE, { x: 9.2, y, w: 3.4, h: 1.25, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 9.2, y, w: 0.1, h: 1.25, fill: { color: wr[2] } });
    s.addText(wr[0], { x: 9.45, y: y + 0.13, w: 3.0, h: 0.4, fontFace: "Consolas", fontSize: 13,
      bold: true, color: NAVY, margin: 0 });
    s.addText(wr[1], { x: 9.45, y: y + 0.52, w: 3.0, h: 0.65, fontFace: BF, fontSize: 11, color: MUTE,
      margin: 0, valign: "top" });
  });

  s.addText("Pricing never leaves HubSpot. Only lifestyle / amenity / location tags reach Fluency.",
    { x: 0.7, y: 6.5, w: 12, h: 0.4, fontFace: BF, fontSize: 13, italic: true, color: NAVY, margin: 0 });
  footer(s, 3);
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDES 4-12 — the line-by-line steps
// ════════════════════════════════════════════════════════════════════════════
stepSlide(1, "The run begins", "A daily trigger fires the sync",
  [ "An internal endpoint kicks off the pipeline: POST /api/internal/fluency-tag-sync.",
    "The schedule itself lives in Render Cron / n8n — not in the code. (Verify the exact daily time there.)",
    "Sample mode runs one property synchronously; full mode runs in the background." ],
  "TRIGGER", [
    "POST /api/internal/",
    "     fluency-tag-sync",
    "",
    "sample:          test N",
    "single_property: test 1",
    "scrape_urls:     on/off",
    "dry_run:         compute-only",
  ], COPPER);

stepSlide(2, "Who's in scope", "Pull the property list from HubSpot",
  [ "Selects every company in RPM Managed / Onboarding / Dispositioning…",
    "…that has an aptiq_property_id set. No Apt IQ ID = the property is skipped.",
    "That's why a brand-new property shows “Not yet computed” until its ID is added." ],
  "SCOPE FILTER", [
    "plestatus IN (",
    "  RPM Managed,",
    "  Onboarding,",
    "  Dispositioning )",
    "AND",
    "aptiq_property_id",
    "  HAS_PROPERTY",
  ], NAVY2);

stepSlide(3, "Source 1 — the facts", "Pull ApartmentIQ data",
  [ "Each property is matched to the Apt IQ daily CSV by its aptiq_property_id.",
    "Apt IQ provides the hard facts — amenities, floor plans, year built, rent, occupancy, exposure.",
    "From those, the engine derives voice tier, lifecycle, rent percentile, and competitors." ],
  "WHAT APT IQ GIVES", [
    "amenities",
    "floor_plans",
    "year_built / renovated",
    "avg_rent  (HubSpot only)",
    "concessions",
    "occupancy / exposure",
    "→ voice tier, lifecycle,",
    "  percentile, competitors",
  ], SAGE);

stepSlide(4, "Source 2 — the voice", "Scrape the property website",
  [ "When enabled, Claude reads the property's own marketing site.",
    "It extracts the marketing-voice fields the CSV can't provide.",
    "~$0.02 and ~10–20 seconds per property. Falls back to stored values when a run skips it." ],
  "WHAT THE SITE GIVES", [
    "unit_noun",
    "marketed_amenity_names",
    "amenities_descriptions",
    "neighborhood",
    "landmarks",
    "nearby_employers",
  ], COPPER);

stepSlide(5, "Source 3 — the context", "Pull ClickUp intake forms",
  [ "Property-specific guidance captured at onboarding (phase 2.3).",
    "Feeds the guardrail fields copy must honor or avoid.",
    "Where the team's human knowledge enters the pipeline directly." ],
  "WHAT FORMS GIVE", [
    "must_include",
    "forbidden_phrases",
    "lease_signal_text",
    "struggling_units",
    "insider_color",
  ], NAVY2);

stepSlide(6, "The merge", "tag_builder composes the tags",
  [ "All three sources feed one engine that produces the final fluency_* values.",
    "Facts become marketing tags: rent percentile → voice tier; year + occupancy → lifecycle.",
    "Fresh data always beats stale; a key is written only when there's a real value for it." ],
  "DERIVED FIELDS", [
    "voice_tier",
    "  = f(rent percentile)",
    "lifecycle_state",
    "  = f(year, occ, exposure)",
    "rent_percentile",
    "  = vs same-metro peers",
    "competitors",
    "  = same Apt IQ market",
  ], COPPER);

stepSlide(7, "The human layer", "Property Marketing overrides win",
  [ "Any auto-derived field can be overridden in the Community Brief.",
    "An override writes to that field's fluency_*_override property.",
    "On every future sync, the override is used instead of the derived value — permanently, until cleared." ],
  "OVERRIDE > PIPELINE", [
    "derived:  “standard”",
    "override: “lifestyle”",
    "        ↓",
    "every sync uses",
    "  “lifestyle”",
    "",
    "PM knowledge beats",
    "the algorithm",
  ], SAGE);

stepSlide(8, "The safety check", "The autonomy gate",
  [ "Before any live write, a data-quality gate runs.",
    "If a check fails, the write is BLOCKED (must pass commit_override to force).",
    "This is what stops one bad data day from corrupting every property's tags." ],
  "GATE CHECKS", [
    "✗ unmatched properties",
    "✗ off-vocab voice tier",
    "✗ off-vocab lifecycle",
    "✗ bad floor-plan tokens",
    "✗ invalid avg rent",
    "",
    "all pass → write",
    "any fail → 422 blocked",
  ], NAVY2);

stepSlide(9, "The writes", "Two destinations, one source of truth",
  [ "WRITE 1 — HubSpot company fluency_* properties: the FULL set, including pricing. Powers /accounts/property.",
    "WRITE 2 — the “RPM Property Tag Source” Google Sheet: the marketing-safe subset only.",
    "The sheet is keyed by uuid — the join key that links a row to the right Fluency account." ],
  "TWO TARGETS", [
    "HubSpot fluency_*",
    "  full + pricing",
    "  → dashboard",
    "",
    "Google Sheet",
    "  subset, no pricing",
    "  key = uuid",
    "  → Fluency reads",
  ], COPPER);

stepSlide(10, "The payoff", "Fluency builds the ads",
  [ "Fluency reads the Google Sheet, matching each row to an account by uuid.",
    "It uses the tags — voice, amenities, neighborhood, guardrails — to generate paid-media copy + targeting.",
    "The loop closes: property facts in, on-brand compliant ads out, refreshed daily." ],
  "FLUENCY USES", [
    "voice_tier   → tone",
    "amenities    → copy",
    "neighborhood → local",
    "competitors  → position",
    "forbidden_*  → exclude",
    "",
    "= on-brand,",
    "  compliant ads",
  ], SAGE);

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 14 — How PM edits it (the loop) — light, full-width feature
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addText("FOR PROPERTY MARKETING", { x: 0.7, y: 0.6, w: 11, h: 0.4, fontFace: BF, fontSize: 14,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText("How you edit it — and why your edit sticks", { x: 0.7, y: 1.0, w: 12, h: 0.8,
    fontFace: HF, fontSize: 28, bold: true, color: NAVY, margin: 0 });

  const badges = [
    ["Edited", "you set an override — wins", COPPER],
    ["Pipeline", "auto-derived from data", SAGE],
    ["Not set", "editable, nothing yet", NAVY2],
    ["Pending", "Apt IQ hasn't computed", MUTE],
  ];
  badges.forEach((b, i) => {
    const x = 0.7 + i * 3.05;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 2.1, w: 2.85, h: 1.5, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 2.1, w: 2.85, h: 0.12, fill: { color: b[2] } });
    s.addText(b[0], { x: x + 0.25, y: 2.4, w: 2.4, h: 0.5, fontFace: HF, fontSize: 19, bold: true,
      color: NAVY, margin: 0 });
    s.addText(b[1], { x: x + 0.25, y: 2.95, w: 2.4, h: 0.6, fontFace: BF, fontSize: 12, color: MUTE,
      margin: 0, valign: "top" });
  });

  s.addText("The edit loop", { x: 0.7, y: 4.0, w: 6, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
    color: COPPER, charSpacing: 1, margin: 0 });
  s.addText([
    { text: "Open the property's Community Brief → see one value per field + its source badge.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 8 } },
    { text: "Edit a field → it writes to fluency_*_override on the HubSpot company.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 8 } },
    { text: "Next daily sync uses YOUR value, not the derived one — and keeps using it until you clear it.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 8 } },
    { text: "Apt IQ facts (year built, floor plans) are read-only — they're ground truth.", options: { bullet: { code: "2022", indent: 14 } } },
  ], { x: 0.7, y: 4.45, w: 11.8, h: 2.4, fontFace: BF, fontSize: 16, color: INK, valign: "top" });
  footer(s, 14);
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 15 — Fair Housing + what to know
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addText("GUARDRAILS & WHAT TO KNOW", { x: 0.7, y: 0.6, w: 11, h: 0.4, fontFace: BF, fontSize: 14,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText("The protections + the things that trip people up", { x: 0.7, y: 1.0, w: 12, h: 0.8,
    fontFace: HF, fontSize: 26, bold: true, color: NAVY, margin: 0 });

  // Fair Housing card
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.1, w: 5.9, h: 4.4, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.1, w: 5.9, h: 0.6, fill: { color: SAGE } });
  s.addText("FAIR HOUSING", { x: 0.95, y: 2.1, w: 5.4, h: 0.6, valign: "middle", fontFace: BF,
    fontSize: 15, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText([
    { text: "Pricing never reaches Fluency. Avg rent, concessions, and percentile stay in HubSpot only.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } },
    { text: "Only lifestyle / amenity / location tags flow to ad copy.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } },
    { text: "“Forbidden phrases” lets you hard-exclude anything sensitive from copy.", options: { bullet: { code: "2022", indent: 14 } } },
  ], { x: 0.95, y: 2.95, w: 5.4, h: 3.3, fontFace: BF, fontSize: 15, color: INK, valign: "top" });

  // What to know card
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.1, w: 5.7, h: 4.4, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.1, w: 5.7, h: 0.6, fill: { color: NAVY2 } });
  s.addText("WHAT TRIPS PEOPLE UP", { x: 7.15, y: 2.1, w: 5.2, h: 0.6, valign: "middle", fontFace: BF,
    fontSize: 15, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText([
    { text: "No aptiq_property_id → property is skipped entirely.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } },
    { text: "No uuid → HubSpot fields write, but it's skipped on the Fluency sheet (no ad target).", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } },
    { text: "Website scrape is opt-in per run; otherwise stored values are reused.", options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } },
    { text: "The schedule lives in Render / n8n — not the codebase.", options: { bullet: { code: "2022", indent: 14 } } },
  ], { x: 7.15, y: 2.95, w: 5.2, h: 3.3, fontFace: BF, fontSize: 15, color: INK, valign: "top" });
  footer(s, 15);
}

// ════════════════════════════════════════════════════════════════════════════
// SLIDE 16 — Takeaway (dark)
// ════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.28, fill: { color: COPPER } });
  s.addText("THE TAKEAWAY", { x: 0.9, y: 1.4, w: 11, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
    color: COPPER, charSpacing: 3, margin: 0 });
  s.addText("Facts in. Tags out. Refreshed daily. Property Marketing steers.",
    { x: 0.9, y: 2.0, w: 11.5, h: 2.0, fontFace: HF, fontSize: 38, bold: true, color: WHITE, margin: 0 });
  s.addText([
    { text: "ApartmentIQ + the property website feed one merge engine.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "Output lands in HubSpot (full) + the Fluency sheet (marketing-safe subset, keyed by uuid).", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "Property Marketing overrides any field in the Community Brief — and the override wins.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5" } },
  ], { x: 0.9, y: 4.3, w: 11.5, h: 2.2, fontFace: BF, fontSize: 17, valign: "top" });
  s.addText("Full detail: docs/FLUENCY_PIPELINE.md  ·  Editing surface: docs/CLIENT_BRIEF_SYSTEM.md",
    { x: 0.9, y: 6.8, w: 11.5, h: 0.4, fontFace: BF, fontSize: 12, color: "9AA7B4", margin: 0 });
}

const out = path.join(os.homedir(), "Downloads", "RPM_Property_Tag_Pipeline.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("WROTE " + out));
