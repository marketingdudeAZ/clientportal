/* Detailed team briefing — Community Brief → Fluency. Covers the 4-layer
 * data model, the 3 capture paths, the LLM passes, an end-to-end field
 * trace, the daily tag-sync, Fair Housing guardrails, and the phased
 * rollout plan. RPM brand, numbered motif, source-badge UX.
 * Run: node scripts/gen_community_brief_to_fluency_pptx.js
 * Output: ~/Downloads/RPM_Community_Brief_to_Fluency.pptx
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
pres.title = "RPM Community Brief → Fluency";
const W = 13.33, H = 7.5;

const sh = () => ({ type: "outer", color: "000000", blur: 7, offset: 3, angle: 135, opacity: 0.13 });

function footer(slide, n) {
  slide.addText("RPM Community Brief → Fluency — detailed team briefing",
    { x: 0.6, y: H - 0.42, w: 9, h: 0.3, fontFace: BF, fontSize: 9, color: MUTE, align: "left", margin: 0 });
  slide.addText(String(n), { x: W - 1.0, y: H - 0.42, w: 0.5, h: 0.3,
    fontFace: BF, fontSize: 9, color: MUTE, align: "right", margin: 0 });
}

function lightHeader(s, kicker, title, titleSize) {
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addText(kicker.toUpperCase(), { x: 0.7, y: 0.45, w: 12, h: 0.35, fontFace: BF, fontSize: 13,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 0.7, y: 0.83, w: 12.2, h: 0.85, fontFace: HF, fontSize: titleSize || 25,
    bold: true, color: NAVY, margin: 0 });
}

function panelEntries(s, x, y, w, entries) {
  let yy = y;
  entries.forEach((e) => {
    s.addText(e[0], { x: x + 0.28, y: yy, w: w - 0.5, h: 0.32, fontFace: BF, fontSize: 13.5,
      bold: true, color: NAVY, margin: 0 });
    s.addText(e[1], { x: x + 0.28, y: yy + 0.32, w: w - 0.5, h: 0.5, fontFace: BF, fontSize: 10.5,
      color: MUTE, margin: 0, valign: "top" });
    yy += 0.92;
  });
}

function stepSlide(n, posNum, kicker, title, whatLines, cardTitle, cardRows, cardAccent) {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addShape(pres.shapes.OVAL, { x: 0.7, y: 0.55, w: 1.05, h: 1.05, fill: { color: NAVY }, shadow: sh() });
  s.addText(String(n), { x: 0.7, y: 0.55, w: 1.05, h: 1.05, align: "center", valign: "middle",
    fontFace: HF, fontSize: 36, bold: true, color: COPPER_LT, margin: 0 });
  s.addText(kicker.toUpperCase(), { x: 2.0, y: 0.6, w: 7.5, h: 0.32, fontFace: BF, fontSize: 12,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 2.0, y: 0.92, w: 7.5, h: 0.78, fontFace: HF, fontSize: 24, bold: true,
    color: NAVY, margin: 0 });
  s.addText(whatLines.map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 },
    breakLine: true, paraSpaceAfter: 8 } })),
    { x: 2.05, y: 1.95, w: 5.5, h: 4.7, fontFace: BF, fontSize: 14, color: INK, valign: "top" });
  const cardX = 8.0, cardY = 1.85, cardW = 4.8, cardH = 4.8;
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: cardH,
    fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: 0.6, fill: { color: cardAccent || NAVY2 } });
  s.addText(cardTitle, { x: cardX + 0.25, y: cardY, w: cardW - 0.5, h: 0.6, valign: "middle",
    fontFace: BF, fontSize: 12, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText(cardRows.map((r) => ({ text: r, options: { bullet: false, breakLine: true, paraSpaceAfter: 6 } })),
    { x: cardX + 0.3, y: cardY + 0.8, w: cardW - 0.6, h: cardH - 1.0, fontFace: "Consolas",
      fontSize: 11, color: INK, valign: "middle" });
  footer(s, posNum);
  return s;
}

// ════ SLIDE 1 — Title (dark) ════
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.28, fill: { color: COPPER } });
  s.addText("RPM LIVING  ·  DIGITAL PRODUCTS & SERVICES", { x: 0.9, y: 1.5, w: 11.5, h: 0.4,
    fontFace: BF, fontSize: 14, bold: true, color: COPPER, charSpacing: 3, margin: 0 });
  s.addText("Community Brief → Fluency", { x: 0.9, y: 2.1, w: 11.7, h: 1.3,
    fontFace: HF, fontSize: 48, bold: true, color: WHITE, margin: 0 });
  s.addText("How property data moves from edit to ad — the architecture, the data model, the rollout plan",
    { x: 0.9, y: 3.5, w: 11.5, h: 0.9, fontFace: BF, fontSize: 19, color: COPPER_LT, margin: 0 });
  s.addText("Detailed team briefing  ·  Digital Products & Services", { x: 0.9, y: 6.4, w: 11, h: 0.4,
    fontFace: BF, fontSize: 13, color: "9AA7B4", margin: 0 });
}

// ════ SLIDE 2 — Flywheel ════
{
  const s = pres.addSlide();
  lightHeader(s, "The big idea", "One brief per community — better in, better out", 24);
  const stages = [
    ["Capture", "AptIQ + website + AI", COPPER],
    ["Review & Edit", "Property Marketing steers", SAGE],
    ["Publish", "Lands in HubSpot", NAVY2],
    ["Activate", "Fluency builds the ads", COPPER],
  ];
  const cw = 2.75, cy = 2.5, ch = 1.7;
  const xs = [0.7, 3.75, 6.8, 9.85];
  stages.forEach((st, i) => {
    const x = xs[i];
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: cy, w: cw, h: ch, rectRadius: 0.12,
      fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: cw, h: 0.12, fill: { color: st[2] } });
    s.addText(String(i + 1), { x: x + 0.2, y: cy + 0.28, w: 0.8, h: 0.5, fontFace: HF, fontSize: 26,
      bold: true, color: st[2], margin: 0 });
    s.addText(st[0], { x: x + 0.25, y: cy + 0.82, w: cw - 0.45, h: 0.45, fontFace: HF, fontSize: 16,
      bold: true, color: NAVY, margin: 0 });
    s.addText(st[1], { x: x + 0.25, y: cy + 1.24, w: cw - 0.45, h: 0.4, fontFace: BF, fontSize: 11,
      color: MUTE, margin: 0 });
    if (i < 3) {
      s.addShape(pres.shapes.LINE, { x: x + cw + 0.04, y: cy + ch / 2, w: 0.22, h: 0,
        line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
    }
  });
  s.addShape(pres.shapes.LINE, { x: 0.95, y: 5.05, w: 11.2, h: 0,
    line: { color: SAGE, width: 2.5, beginArrowType: "triangle" } });
  s.addText("Better data in  →  sharper campaigns  →  more attribution signal  →  better data next cycle.",
    { x: 0.7, y: 5.35, w: 12, h: 0.6, fontFace: BF, fontSize: 14, italic: true, color: NAVY, align: "center", margin: 0 });
  s.addText("The whole rollout is about turning more properties into this loop — gradually, on purpose.",
    { x: 0.7, y: 6.0, w: 12, h: 0.5, fontFace: BF, fontSize: 13, color: MUTE, align: "center", margin: 0 });
  footer(s, 2);
}

// ════ SLIDE 3 — Architecture: 4 data layers ════
{
  const s = pres.addSlide();
  lightHeader(s, "The architecture", "Four layers — sources, source of truth, workflow store, ad reader", 22);
  const layers = [
    ["LAYER 1 — SOURCES",
     "AptIQ daily CSV  ·  Property website (multi-page scrape)  ·  ClickUp intake  ·  Pitch decks / RFPs  ·  Property Marketing edits", SAGE,
     "Raw facts about the property. Some auto, some human."],
    ["LAYER 2 — SOURCE OF TRUTH (HubSpot)",
     "Company record  ·  fluency_* properties + fluency_*_override mirrors  ·  Override wins on every sync", COPPER,
     "Every brief field lives here — both the auto value and the human override."],
    ["LAYER 3 — WORKFLOW STORE (HubDB)",
     "rpm_property_briefs  ·  rpm_brief_drafts  ·  rpm_content_briefs  ·  Token-protected approval portal", NAVY2,
     "Brief documents (markdown) + AI drafts + SEO content briefs. Separate from the field-level data."],
    ["LAYER 4 — AD READER (Fluency)",
     "“RPM Property Tag Source” Google Sheet  →  Fluency platform  →  Ad copy + targeting", COPPER,
     "Daily sync writes from HubSpot here. Fluency reads ONLY this sheet, keyed by uuid."],
  ];
  layers.forEach((L, i) => {
    const y = 1.85 + i * 1.18;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 11.9, h: 1.0, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 0.16, h: 1.0, fill: { color: L[2] } });
    s.addText(L[0], { x: 1.0, y: y + 0.1, w: 6.5, h: 0.32, fontFace: BF, fontSize: 12.5,
      bold: true, color: NAVY, charSpacing: 1, margin: 0 });
    s.addText(L[3], { x: 7.5, y: y + 0.1, w: 5.0, h: 0.32, fontFace: BF, fontSize: 10.5,
      italic: true, color: L[2], margin: 0, align: "right" });
    s.addText(L[1], { x: 1.0, y: y + 0.42, w: 11.4, h: 0.5, fontFace: BF, fontSize: 12, color: INK,
      margin: 0, valign: "top" });
  });
  s.addText("Data flows down. Property Marketing edits flow into Layer 2 from the side and beat every auto-value below.",
    { x: 0.7, y: 6.7, w: 12, h: 0.35, fontFace: BF, fontSize: 11.5, italic: true, color: MUTE, align: "center", margin: 0 });
  footer(s, 3);
}

// ════ SLIDE 4 — Community Brief vs Fluency Tag Pipeline (clear the confusion) ════
{
  const s = pres.addSlide();
  lightHeader(s, "Two names, one system", "Community Brief and the Fluency Tag Pipeline — what each one is", 23);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.95, w: 5.85, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.95, w: 5.85, h: 0.6, fill: { color: COPPER } });
  s.addText("COMMUNITY BRIEF", { x: 0.95, y: 1.95, w: 5.4, h: 0.6, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "The human-facing data structure. 12 sections, ~45 editable fields.",
    "Lives ON the HubSpot company record as fluency_*_override properties.",
    "Editing surfaces: approval portal, /accounts/property, ClickUp intake.",
    "Approval portal stores a markdown rendering in HubDB rpm_property_briefs.",
    "Override-wins: every human edit beats every auto-derived value.",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 9 } })),
    { x: 0.95, y: 2.75, w: 5.4, h: 3.5, fontFace: BF, fontSize: 13, color: INK, valign: "top" });

  s.addShape(pres.shapes.RECTANGLE, { x: 6.75, y: 1.95, w: 5.85, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.75, y: 1.95, w: 5.85, h: 0.6, fill: { color: SAGE } });
  s.addText("FLUENCY TAG PIPELINE", { x: 7.0, y: 1.95, w: 5.4, h: 0.6, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "The automated daily sync. Reads HubSpot + AptIQ + (optional) website scrape.",
    "Merges via tag_builder.py: override > resolved > pipeline default.",
    "Writes TWO destinations: HubSpot fluency_* (the resolved set) + Google Sheet.",
    "Sheet = “RPM Property Tag Source,” keyed by uuid. Fluency reads ONLY this.",
    "Autonomy gate blocks bad-data days from corrupting tags downstream.",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 9 } })),
    { x: 7.0, y: 2.75, w: 5.4, h: 3.5, fontFace: BF, fontSize: 13, color: INK, valign: "top" });
  s.addText("Same fluency_* properties. Two different concerns. Brief = how it's edited. Pipeline = how it's distributed.",
    { x: 0.7, y: 6.7, w: 12, h: 0.35, fontFace: BF, fontSize: 11.5, italic: true, color: NAVY, align: "center", margin: 0 });
  footer(s, 4);
}

// ════ SLIDE 5 — Layer 2 deep dive: HubSpot fluency_* properties ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 2 — HubSpot company properties", "12 sections, ~45 editable fields, override-wins everywhere", 21);
  const cols = [
    [["Voice & Positioning", "voice_tier, unit_noun, advertised_name, short_name, former_property_name"],
     ["Brand & Story", "taglines, brand_adjectives, differentiators, selling_points, residents_love, residents_dislike*, target_resident*"],
     ["Lifecycle", "lifecycle_state (pre_lease / lease_up / stabilized / renovated)"],
     ["Inventory", "floor_plans_json (structured: name, beds, baths, sq ft from AptIQ floor_plan report)"]],
    [["Amenities (split)", "property_amenities (community-level), unit_features (in-unit), marketed_amenity_names, amenities_descriptions"],
     ["Geography", "neighborhood (IN), nearby_neighborhoods (NEAR), landmarks (CLOSE TO), neighborhood_highlights, nearby_employers"],
     ["Competitors", "competitors — same-market peers used for positioning"],
     ["Strategy & Goals", "goals, initiatives, onsite_developments, local_partnerships, onsite_events  ·  challenges*, priorities*, website_priorities*"]],
    [["Operations & Tech*", "marketing_budget, pms, cms, chatbot, website_last_updated, building_style, asset_class, elise_ai, crm, host_name"],
     ["Guardrails", "must_include, forbidden_phrases, excluded_neighborhoods, client_expectations"],
     ["Tracking & Attribution", "13-source table (Brochure → Social Posting) — phone + UTM per source — stored as tracking_json"],
     ["Documents", "Pitch decks, RFPs, brand guides — list of {label, url, kind}"]],
  ];
  const xs = [0.7, 4.92, 9.14], cw = 3.95, cy = 1.9, ch = 4.85;
  cols.forEach((entries, i) => {
    const x = xs[i];
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: cw, h: ch, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: 0.1, h: ch, fill: { color: [COPPER, SAGE, NAVY2][i] } });
    panelEntries(s, x, cy + 0.25, cw, entries);
  });
  s.addText("Asterisk (*) = internal-only — stored + editable but NEVER sent to Fluency. Fair Housing-safe by construction.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, italic: true, color: MUTE, align: "center", margin: 0 });
  footer(s, 5);
}

// ════ SLIDE 6 — Layer 3 deep dive: HubDB tables ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 3 — HubDB workflow tables", "Three tables, three purposes — none of them are what Fluency reads", 22);
  const tables = [
    ["rpm_property_briefs", "id 281385088", COPPER,
     "Token-protected brief markdown. 193+ rows captured so far.",
     ["What:  the Community Brief AS A DOCUMENT (markdown)",
      "Why:   approval portal renders this for review/edit/approve",
      "Key:   token (unguessable per-brief secret)",
      "Joined to:  HubSpot company by ticket_id (auto:<cid>)",
      "Lifecycle:  pending_approval → approved (or needs_edits)",
      "Where used:  approval portal at /property-brief/approve/<token>"]],
    ["rpm_brief_drafts", "id 261612102", SAGE,
     "LLM-drafted suggestions before human review.",
     ["What:  pre-approval AI suggestions (Claude Sonnet)",
      "Why:   captures the LLM's best guess before edit",
      "Key:   draft_id",
      "Joined to:  HubSpot company by company_id",
      "Lifecycle:  draft → accepted / rejected",
      "Where used:  internal review UI; populates field defaults"]],
    ["rpm_content_briefs", "id 259015225", NAVY2,
     "SEO content briefs (separate flow — not the Community Brief).",
     ["What:  per-content-piece SEO brief (target keyword, outline)",
      "Why:   feeds the SEO content production team",
      "Key:   content_id",
      "Joined to:  HubSpot company by company_id",
      "Lifecycle:  draft → in_review → published",
      "Where used:  SEO team's content production workflow"]],
  ];
  tables.forEach((t, i) => {
    const x = 0.7 + i * 4.21, w = 3.95;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.9, w, h: 4.85, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.9, w, h: 0.55, fill: { color: t[2] } });
    s.addText(t[0], { x: x + 0.2, y: 1.9, w: w - 0.4, h: 0.55, valign: "middle", fontFace: "Consolas",
      fontSize: 13, bold: true, color: WHITE, margin: 0 });
    s.addText(t[1], { x: x + 0.2, y: 1.9, w: w - 0.4, h: 0.55, valign: "middle", fontFace: BF,
      fontSize: 9.5, color: WHITE, align: "right", margin: 0 });
    s.addText(t[3], { x: x + 0.25, y: 2.55, w: w - 0.5, h: 0.6, fontFace: BF, fontSize: 11.5,
      italic: true, color: NAVY, valign: "top", margin: 0 });
    s.addText(t[4].map((r) => ({ text: r, options: { bullet: false, breakLine: true, paraSpaceAfter: 5 } })),
      { x: x + 0.25, y: 3.2, w: w - 0.5, h: 3.5, fontFace: "Consolas", fontSize: 10.5, color: INK, valign: "top" });
  });
  s.addText("Important:  Fluency does NOT read any of these. These are workflow/document stores. The Fluency reader is the Google Sheet in Layer 4.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, bold: true, color: NAVY, align: "center", margin: 0 });
  footer(s, 6);
}

// ════ SLIDE 7 — Layer 4: what Fluency actually reads ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 4 — the Fluency reader", "“RPM Property Tag Source” Google Sheet, keyed by uuid", 23);
  // Left: HubSpot fluency_* (auto)
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.7, y: 2.2, w: 3.4, h: 1.5, rectRadius: 0.1,
    fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.2, w: 0.1, h: 1.5, fill: { color: COPPER } });
  s.addText("HubSpot company", { x: 0.95, y: 2.32, w: 3.15, h: 0.32, fontFace: BF, fontSize: 13, bold: true, color: NAVY });
  s.addText("fluency_* properties (resolved)", { x: 0.95, y: 2.65, w: 3.15, h: 0.3, fontFace: BF, fontSize: 11, color: MUTE });
  s.addText("Override wins.\nAuto-derived otherwise.", { x: 0.95, y: 2.97, w: 3.15, h: 0.6, fontFace: BF, fontSize: 10, color: INK, valign: "top" });

  // Arrow
  s.addShape(pres.shapes.LINE, { x: 4.15, y: 2.95, w: 0.85, h: 0, line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
  s.addText("daily\nsync", { x: 4.2, y: 2.45, w: 0.75, h: 0.5, fontFace: BF, fontSize: 9.5, italic: true, color: MUTE, align: "center" });

  // Middle: tag_builder
  s.addShape(pres.shapes.RECTANGLE, { x: 5.05, y: 2.2, w: 3.0, h: 1.5, fill: { color: NAVY }, shadow: sh() });
  s.addText("tag_builder.py", { x: 5.05, y: 2.32, w: 3.0, h: 0.35, align: "center", fontFace: "Consolas", fontSize: 12, bold: true, color: COPPER_LT });
  s.addText("MERGE + AUTONOMY GATE", { x: 5.05, y: 2.67, w: 3.0, h: 0.3, align: "center", fontFace: BF, fontSize: 10, bold: true, color: WHITE, charSpacing: 1 });
  s.addText("override > resolved\nbad-data gate blocks live write", { x: 5.05, y: 3.0, w: 3.0, h: 0.65, align: "center", fontFace: BF, fontSize: 10, color: "9AA7B4" });

  // Arrow
  s.addShape(pres.shapes.LINE, { x: 8.1, y: 2.95, w: 0.85, h: 0, line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
  s.addText("write\nsheet", { x: 8.15, y: 2.45, w: 0.75, h: 0.5, fontFace: BF, fontSize: 9.5, italic: true, color: MUTE, align: "center" });

  // Right: Google Sheet
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 9.0, y: 2.2, w: 3.6, h: 1.5, rectRadius: 0.1,
    fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 9.0, y: 2.2, w: 0.1, h: 1.5, fill: { color: SAGE } });
  s.addText("RPM Property Tag Source", { x: 9.25, y: 2.32, w: 3.3, h: 0.32, fontFace: BF, fontSize: 12.5, bold: true, color: NAVY });
  s.addText("(Google Sheet)", { x: 9.25, y: 2.65, w: 3.3, h: 0.3, fontFace: BF, fontSize: 11, color: MUTE });
  s.addText("Keyed by uuid → Fluency\nreads the row for each account.", { x: 9.25, y: 2.97, w: 3.3, h: 0.6, fontFace: BF, fontSize: 10, color: INK, valign: "top" });

  // Lower section: what fluency consumes
  s.addText("WHAT FLUENCY ACTUALLY CONSUMES PER AD", { x: 0.7, y: 4.15, w: 12, h: 0.3, fontFace: BF, fontSize: 12, bold: true, color: COPPER, charSpacing: 2 });
  const uses = [
    ["voice_tier", "→ tone of the copy (value / standard / lifestyle / luxury)"],
    ["amenities (property + unit)", "→ copy bullets, ad extensions, feature callouts"],
    ["neighborhood + landmarks", "→ local relevance signals + headline geo"],
    ["taglines + selling_points", "→ headline + description ad text"],
    ["competitors", "→ positioning context (search keyword strategy)"],
    ["forbidden_phrases + excluded_neighborhoods", "→ hard exclusions before generation"],
  ];
  uses.forEach((u, i) => {
    const y = 4.55 + Math.floor(i / 2) * 0.55, x = 0.7 + (i % 2) * 6.05;
    s.addText(u[0], { x, y, w: 2.7, h: 0.32, fontFace: "Consolas", fontSize: 11, bold: true, color: NAVY });
    s.addText(u[1], { x: x + 2.7, y, w: 3.4, h: 0.32, fontFace: BF, fontSize: 11, color: INK });
  });
  s.addText("Pricing, internal-only fields, and human-only metadata stay in HubSpot. They never reach the sheet.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, italic: true, color: MUTE, align: "center" });
  footer(s, 7);
}

// ════ SLIDE 8 — The 3 capture paths ════
{
  const s = pres.addSlide();
  lightHeader(s, "How a brief gets populated", "Three capture paths — same destination", 23);
  const paths = [
    ["PATH 1 — CLICKUP INTAKE",
     "A New Account Build ticket lands in ClickUp list 901112045284, status “To Vet.”",
     ["Webhook → HubSpot company match/create",
      "Deal + line items + quote drafted",
      "Brief LLM run (markdown stored in HubDB)",
      "Structured-extract LLM run (writes fluency_*_override fields)",
      "Approval URL dropped in ClickUp comment",
      "Kill switch:  BRIEF_LLM_ENABLED=false pauses Path B only"], COPPER],
    ["PATH 2 — AUTO-PLE CAPTURE",
     "Daily cron at 10:00 UTC for every deal-bearing property NOT Disposition Complete.",
     ["Render cron cb-capture-scan fires the endpoint",
      "Scrapes the property site (multi-page)",
      "Markdown brief + structured-extract written",
      "Brief lifecycle: pending_approval",
      "Idempotent: properties with a brief get skipped",
      "Scope: ~1,218 deal-bearing properties today"], SAGE],
    ["PATH 3 — DIRECT HUMAN EDIT",
     "Property Marketing edits a field on /accounts or the approval portal.",
     ["Click any “Editable” / “Override” row → input/dropdown",
      "Save PATCHes /api/accounts/property/field (X-Portal-Email auth)",
      "Writes to the field's fluency_*_override property",
      "Source badge flips to “Override” on save",
      "Daily sync picks up the change next cycle",
      "No LLM cost; immediate effect"], NAVY2],
  ];
  paths.forEach((p, i) => {
    const x = 0.7 + i * 4.21, w = 3.95;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.9, w, h: 4.85, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.9, w, h: 0.55, fill: { color: p[3] } });
    s.addText(p[0], { x: x + 0.2, y: 1.9, w: w - 0.4, h: 0.55, valign: "middle", fontFace: BF,
      fontSize: 12, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
    s.addText(p[1], { x: x + 0.25, y: 2.55, w: w - 0.5, h: 0.85, fontFace: BF, fontSize: 11.5,
      italic: true, color: NAVY, valign: "top", margin: 0 });
    s.addText(p[2].map((r) => ({ text: r, options: { bullet: { code: "2022", indent: 12 }, breakLine: true, paraSpaceAfter: 6 } })),
      { x: x + 0.25, y: 3.45, w: w - 0.5, h: 3.25, fontFace: BF, fontSize: 11, color: INK, valign: "top" });
  });
  footer(s, 8);
}

// ════ SLIDE 9 — The two LLM passes ════
stepSlide(1, 9, "The LLM passes — Path A: markdown", "Generate a reviewable brief document",
  [ "Domain scrape: homepage + /amenities + /floorplans + /neighborhood + /community (up to 5 subpages, 20k chars).",
    "Submitter notes + selected channels passed as user context.",
    "Claude Sonnet drafts a markdown brief: Overview, Target, Voice, Differentiators, Channel Strategy, Metrics.",
    "Persisted in HubDB rpm_property_briefs with a token; URL pushed to ClickUp.",
    "Cost: ~$0.02 per property.",
    "Result: the human-readable brief reviewers approve in the portal." ],
  "PATH A FLOW", [
    "scrape_site_text(domain)",
    "  → 20k chars multi-page",
    "",
    "Claude Sonnet (markdown):",
    "  system: brief sections",
    "  user:   site + notes",
    "",
    "→ rpm_property_briefs row",
    "→ approval URL → ClickUp",
  ], COPPER);

stepSlide(2, 10, "The LLM passes — Path B: structured fields", "Populate the /accounts editor automatically",
  [ "Same scraped text reused (one round-trip per property, not two scrapes).",
    "Claude Sonnet asked for one JSON object: 15 logical fields × {value, confidence}.",
    "Confidence threshold 0.55 — uncertain extractions return null (the UI shows “Editable”).",
    "Each non-null field PATCHed via community_brief.write_field → writes the *_override.",
    "Cost: ~$0.02 per property (~$0.04 total Path A + Path B).",
    "Result: 14 fluency_*_override fields auto-populated on the company record." ],
  "PATH B FLOW", [
    "draft_community_brief_overrides(",
    "  domain, property_name)",
    "",
    "→ JSON: {key: {value, conf}}",
    "",
    "for each key:",
    "  if conf ≥ 0.55:",
    "    write_field(cid, key, val)",
    "",
    "→ source badge: Override",
  ], SAGE);

// ════ SLIDE 11 — Tracing one field end-to-end ════
{
  const s = pres.addSlide();
  lightHeader(s, "End-to-end trace", "“Property Amenities” for 17th Street Lofts — every hop", 21);
  // Vertical timeline
  const stops = [
    ["1", "APTIQ (Layer 1)", "Raw amenities list from the AptIQ daily CSV. Generic categories: Pool, Fitness Center, Pet-Friendly. Limited specificity.",
     'fluency_amenities = "Pool · Fitness Center · Pet-Friendly"', COPPER],
    ["2", "LLM SCRAPE + EXTRACT (Path B)", "Multi-page scrape pulls 17thstreetlofts.com /amenities. Claude extracts marketing-specific phrases.",
     'fluency_property_amenities_override = "Gated Community with Controlled Access · Intercom Entry · Covered Parking · 24-hr Maintenance · …"', SAGE],
    ["3", "PROPERTY MARKETING (optional)", "AM opens /accounts/property?company_id=28669778583, edits the line to add a new resident-favorite. Save PATCHes the override.",
     "Same property, override updated → wins over both auto sources forever", NAVY2],
    ["4", "DAILY TAG SYNC", "tag_builder reads HubSpot, applies override-wins, writes the resolved value to the Google Sheet row for uuid abc-123.",
     "RPM Property Tag Source — column property_amenities = the override value", COPPER],
    ["5", "FLUENCY GENERATES ADS", "Fluency reads the sheet row. Property Amenities becomes copy bullets in Performance Max + Display, plus structured ad assets.",
     'Live ad copy: "Gated entry · Covered parking · 24-hr maintenance · Pet-friendly · Walk to Atlantic Station."', SAGE],
  ];
  stops.forEach((st, i) => {
    const y = 1.9 + i * 0.92;
    s.addShape(pres.shapes.OVAL, { x: 0.7, y: y + 0.05, w: 0.55, h: 0.55, fill: { color: st[4] } });
    s.addText(st[0], { x: 0.7, y: y + 0.05, w: 0.55, h: 0.55, align: "center", valign: "middle",
      fontFace: HF, fontSize: 17, bold: true, color: WHITE });
    s.addText(st[1], { x: 1.4, y, w: 4.2, h: 0.35, fontFace: BF, fontSize: 12, bold: true,
      color: NAVY, charSpacing: 1, margin: 0 });
    s.addText(st[2], { x: 1.4, y: y + 0.35, w: 4.4, h: 0.55, fontFace: BF, fontSize: 10.5,
      color: INK, valign: "top", margin: 0 });
    s.addShape(pres.shapes.RECTANGLE, { x: 6.0, y: y + 0.05, w: 6.5, h: 0.75,
      fill: { color: "F7F8FA" }, line: { color: CARDLINE, width: 1 } });
    s.addText(st[3], { x: 6.15, y: y + 0.1, w: 6.2, h: 0.65, fontFace: "Consolas",
      fontSize: 10.5, color: INK, valign: "middle", margin: 0 });
    if (i < stops.length - 1) {
      s.addShape(pres.shapes.LINE, { x: 0.97, y: y + 0.6, w: 0, h: 0.32,
        line: { color: CARDLINE, width: 2 } });
    }
  });
  footer(s, 11);
}

// ════ SLIDE 12 — The daily sync (tag_builder) ════
{
  const s = pres.addSlide();
  lightHeader(s, "The daily Fluency tag sync", "How HubSpot edits reach the Google Sheet — once a day, autonomy-gated", 22);
  const stops = [
    ["Trigger", "Render Cron cb-capture-scan + n8n. 10:00 UTC daily.",                                  COPPER],
    ["Scope", "Every company with ≥1 deal whose PLE ≠ Disposition Complete (~1,218 today).",            COPPER],
    ["Read", "AptIQ daily CSV + website scrape (opt-in) + HubSpot fluency_* + fluency_*_override.",      SAGE],
    ["Merge", "tag_builder: override > resolved > pipeline default. Per field, per property.",         SAGE],
    ["Gate", "Autonomy gate: unmatched, off-vocab, bad floor-plan tokens, invalid rent → BLOCK.",       NAVY2],
    ["Write 1", "HubSpot fluency_* (the resolved value — includes pricing for internal display).",       COPPER],
    ["Write 2", "“RPM Property Tag Source” Google Sheet (Fluency-safe subset, NO pricing).",            COPPER],
    ["Result", "Fluency reads its sheet on its own schedule and rebuilds ads for the day.",            SAGE],
  ];
  stops.forEach((st, i) => {
    const y = 1.85 + i * 0.6;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 11.9, h: 0.5, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 } });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 0.14, h: 0.5, fill: { color: st[2] } });
    s.addText(st[0], { x: 0.95, y: y + 0.07, w: 1.7, h: 0.36, fontFace: BF, fontSize: 12, bold: true,
      color: NAVY, charSpacing: 1.2, margin: 0 });
    s.addText(st[1], { x: 2.7, y: y + 0.07, w: 9.8, h: 0.36, fontFace: BF, fontSize: 12, color: INK, margin: 0 });
  });
  s.addText("Pricing never crosses into the Sheet. The autonomy gate is the safety stop — one bad CSV day can't ship broken copy.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, italic: true, color: NAVY, align: "center" });
  footer(s, 12);
}

// ════ SLIDE 13 — Fair Housing guardrails ════
{
  const s = pres.addSlide();
  lightHeader(s, "Fair Housing — guardrails by design", "What never flows to Fluency, no matter what", 23);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.95, w: 11.9, h: 4.7, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.95, w: 11.9, h: 0.6, fill: { color: SAGE } });
  s.addText("FAIR HOUSING — BUILT IN", { x: 0.95, y: 1.95, w: 11.4, h: 0.6, valign: "middle",
    fontFace: BF, fontSize: 14, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "PRICING NEVER CROSSES. avg_rent, concessions, concession_value, rent_percentile, floor-plan rent → stay in HubSpot only. The Google Sheet writer excludes them by enum.",
    "INTERNAL FIELDS NEVER REACH AD COPY. residents_dislike, target_resident, marketing_budget, pms, cms, crm, chatbot, building_style, asset_class, elise_ai, host_name, challenges, priorities, website_priorities, lease_signal_text, struggling_units, insider_color. Stored + editable + visible to the AM team — never sent to Fluency.",
    "FORBIDDEN PHRASES IS A HARD STOP. forbidden_phrases / forbidden_phrases_override is a copy-blacklist. The Sheet ships it; Fluency excludes those phrases at generation time.",
    "NEIGHBORHOODS NOT TO TARGET steer keyword + geo intent away from sensitive areas, without using a geo-fence (which can itself be a Fair Housing risk).",
    "LLM PROMPTS FORBID DEMOGRAPHIC LANGUAGE. The target_resident prompt explicitly bans age, family status, race, religion, national origin, and disability — “describe by LIFESTYLE and NEEDS only.”",
    "ALL AD COPY GOES THROUGH FLUENCY'S OWN FAIR HOUSING FILTER. We're a second line of defense, not the only one.",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 9 } })),
    { x: 1.0, y: 2.75, w: 11.3, h: 3.85, fontFace: BF, fontSize: 12, color: INK, valign: "top" });
  footer(s, 13);
}

// ════ SLIDE 14 — Where Property Marketing edits (the four surfaces) ════
{
  const s = pres.addSlide();
  lightHeader(s, "Where Property Marketing edits", "Four surfaces — same fluency_*_override destination", 22);
  const surfaces = [
    ["1. Approval portal", COPPER,
     "Token URL on the company record (rpm_brief_approval_url). Per-property, unguessable.",
     "Best for:  first-time onboarding review",
     "Edit pattern: inline edit per field, then Approve to publish status."],
    ["2. /accounts/property", SAGE,
     "HubSpot CMS page on digital.rpmliving.com — already in the AM workflow.",
     "Best for:  steady-state edits + ongoing maintenance",
     "Edit pattern: click any “Editable” row, dropdowns where applicable, save flips badge to Override."],
    ["3. ClickUp intake", NAVY2,
     "The intake ticket itself — channel selections, setup fees, notes.",
     "Best for:  setting up a new account build",
     "Edit pattern: custom fields on the parent ticket; webhook does the rest."],
    ["4. Documents", COPPER,
     "Pitch decks, RFPs, brand guides uploaded as document links on /accounts.",
     "Best for:  bringing in the source material the LLM should read",
     "Edit pattern: paste a URL with label + kind. LLM picks it up on next capture."],
  ];
  surfaces.forEach((sf, i) => {
    const y = 1.9 + Math.floor(i / 2) * 2.45, x = 0.7 + (i % 2) * 6.05;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 5.85, h: 2.25, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 5.85, h: 0.5, fill: { color: sf[1] } });
    s.addText(sf[0], { x: x + 0.2, y, w: 5.5, h: 0.5, valign: "middle", fontFace: BF,
      fontSize: 13, bold: true, color: WHITE, charSpacing: 1 });
    s.addText(sf[2], { x: x + 0.25, y: y + 0.6, w: 5.4, h: 0.4, fontFace: BF, fontSize: 11.5,
      italic: true, color: NAVY });
    s.addText(sf[3], { x: x + 0.25, y: y + 1.05, w: 5.4, h: 0.35, fontFace: BF, fontSize: 11, color: INK });
    s.addText(sf[4], { x: x + 0.25, y: y + 1.45, w: 5.4, h: 0.7, fontFace: BF, fontSize: 10.5, color: MUTE, valign: "top" });
  });
  s.addText("All four surfaces write to the same fluency_*_override properties on the HubSpot company record. Override wins on every sync.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, italic: true, color: NAVY, align: "center" });
  footer(s, 14);
}

// ════ SLIDE 15 — Source badges + override-wins UX ════
{
  const s = pres.addSlide();
  lightHeader(s, "How to read the editor", "The source badges on every row", 24);
  const badges = [
    ["PIPELINE", COPPER,
     "Auto-derived value is in place from the daily Fluency tag sync.",
     "Action: leave it; or override below.",
     'voice_tier = "standard"'],
    ["PIPELINE PENDING", MUTE,
     "Field will be auto-derived but hasn't computed yet (e.g. AptIQ pending).",
     "Action: wait or set the override.",
     "Apt IQ match pending"],
    ["EDITABLE", SAGE,
     "Empty + editable. The pipeline doesn't produce this field; it's human-input.",
     "Action: click and add value.",
     'taglines: "Click to add"'],
    ["OVERRIDE", NAVY2,
     "Human-set value is in place. Wins over auto-derived forever (until cleared).",
     "Action: leave it; or edit again.",
     'voice_tier_override = "lifestyle"'],
    ["HUBSPOT", "8B98A7",
     "Read-only HubSpot system field (identity, address, etc.). Not editable here.",
     "Action: edit in HubSpot if needed.",
     'name = "17th Street Lofts"'],
  ];
  badges.forEach((b, i) => {
    const y = 1.95 + i * 0.85;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 2.3, h: 0.65, fill: { color: b[1] } });
    s.addText(b[0], { x: 0.7, y, w: 2.3, h: 0.65, align: "center", valign: "middle",
      fontFace: BF, fontSize: 11.5, bold: true, color: WHITE, charSpacing: 1.2 });
    s.addText(b[2], { x: 3.2, y: y + 0.05, w: 6.5, h: 0.32, fontFace: BF, fontSize: 12, bold: true, color: NAVY });
    s.addText(b[3], { x: 3.2, y: y + 0.35, w: 6.5, h: 0.32, fontFace: BF, fontSize: 11, color: MUTE });
    s.addShape(pres.shapes.RECTANGLE, { x: 9.9, y: y + 0.05, w: 2.7, h: 0.55, fill: { color: "F7F8FA" }, line: { color: CARDLINE, width: 1 } });
    s.addText(b[4], { x: 10.0, y: y + 0.05, w: 2.5, h: 0.55, valign: "middle",
      fontFace: "Consolas", fontSize: 10, color: NAVY });
  });
  s.addText("Rule of thumb: human-set Overrides should be reserved for things you KNOW the algorithm got wrong. Otherwise let the pipeline run.",
    { x: 0.7, y: 6.7, w: 12, h: 0.4, fontFace: BF, fontSize: 12, italic: true, color: NAVY, align: "center" });
  footer(s, 15);
}

// ════ SLIDE 16 — Rollout phases ════
{
  const s = pres.addSlide();
  lightHeader(s, "Rollout plan", "Phased — gradual, on purpose, attribution-driven", 23);
  const phases = [
    // Color rotation chosen for contrast against WHITE theme text — Phase 1
    // + 4 use SAGE/NAVY (not COPPER) so the theme label stays readable.
    ["PHASE 1", "TODAY", "Foundation",
     ["3 demo properties fully populated (17th Street, Arbor Crossing, Muse at Winter Garden)",
      "193 briefs in HubDB rpm_property_briefs",
      "Live /accounts/property editor with inline edit, dropdowns, tracking + documents sections",
      "Daily cron + bulk script ready"], SAGE],
    ["PHASE 2", "THIS WEEK", "Bulk fill",
     ["Run scripts/bulk_capture_briefs.py --force on ~1,218 deal-bearing properties",
      "~3-4 hours runtime, ~$48 Anthropic spend",
      "~15-20% of properties get rich auto-content; rest get placeholder briefs for human fill",
      "BRIEF_LLM_ENABLED kill switch protects against credit exhaustion"], COPPER],
    ["PHASE 3", "30 DAYS", "Coverage",
     ["Wire ScrapingBee or ZenRows as fallback for Cloudflare/Akamai-blocked sites (~$50/mo)",
      "Bumps coverage from ~20% rich to ~80%+ rich auto-populated",
      "Empty-scrape short-circuit so daily cron stops burning LLM on blank sites",
      "Slack digest at end of each daily run"], NAVY2],
    ["PHASE 4", "60-90 DAYS", "Closed loop",
     ["Property Marketing rolls out Tracking & Attribution row entries property by property",
      "Stale-brief refresh cron — re-LLM any brief older than 90 days (catches site updates)",
      "Hyly Convert-layer data feeds back attribution per channel",
      "Fluency reads richer data → better attribution → next cycle even better"], NAVY],
  ];
  phases.forEach((p, i) => {
    const y = 1.9 + i * 1.18;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 11.9, h: 1.05, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 1.5, h: 1.05, fill: { color: p[4] } });
    s.addText(p[0], { x: 0.72, y: y + 0.12, w: 1.46, h: 0.28, align: "center", fontFace: BF, fontSize: 11, bold: true, color: WHITE, charSpacing: 1.5 });
    s.addText(p[1], { x: 0.72, y: y + 0.4, w: 1.46, h: 0.28, align: "center", fontFace: BF, fontSize: 10, color: WHITE });
    s.addText(p[2], { x: 0.72, y: y + 0.68, w: 1.46, h: 0.32, align: "center", fontFace: HF, fontSize: 13, bold: true, color: WHITE, margin: 0 });
    s.addText(p[3].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 12 }, breakLine: true, paraSpaceAfter: 2 } })),
      { x: 2.4, y: y + 0.1, w: 10.1, h: 0.9, fontFace: BF, fontSize: 11, color: INK, valign: "top" });
  });
  footer(s, 16);
}

// ════ SLIDE 17 — What's already live + what to know ════
{
  const s = pres.addSlide();
  lightHeader(s, "What's already in production", "As of June 2026 — the foundations your team can rely on", 22);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.9, w: 5.9, h: 4.8, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.9, w: 5.9, h: 0.55, fill: { color: SAGE } });
  s.addText("LIVE — IN PRODUCTION TODAY", { x: 0.95, y: 1.9, w: 5.4, h: 0.55, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1.5 });
  s.addText([
    "51 new HubSpot company properties via migration",
    "/accounts/property editor with inline edit + dropdowns",
    "Brand & Story · Strategy & Goals · Ops & Tech · Tracking · Documents sections",
    "Multi-page scrape (homepage + amenities + floorplans + etc.)",
    "2 LLM passes per property (markdown + structured)",
    "Override-wins via 4 edit surfaces",
    "Auto-PLE cron scoped to ~1,218 deal-bearing properties",
    "ClickUp → HubSpot deal/quote (PR #13 — no more runaway deals)",
    "BRIEF_LLM_ENABLED kill switch (credit-control)",
    "Bulk capture script ready (scripts/bulk_capture_briefs.py)",
  ].map((t) => ({ text: t, options: { bullet: { code: "2713", indent: 14 }, breakLine: true, paraSpaceAfter: 5 } })),
    { x: 0.95, y: 2.65, w: 5.4, h: 4.0, fontFace: BF, fontSize: 11.5, color: INK, valign: "top" });

  s.addShape(pres.shapes.RECTANGLE, { x: 6.85, y: 1.9, w: 5.75, h: 4.8, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.85, y: 1.9, w: 5.75, h: 0.55, fill: { color: COPPER } });
  s.addText("FAQ — WHAT YOUR TEAM WILL ASK", { x: 7.1, y: 1.9, w: 5.4, h: 0.55, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1.5 });
  s.addText([
    "Q: A field is empty on /accounts. Whose job is it?",
    "  → If badge says EDITABLE: yours. If PIPELINE PENDING: the pipeline (wait or set override).",
    "",
    "Q: Property got rebranded — how do tags refresh?",
    "  → Edit fluency_*_override on /accounts. Pipeline picks it up next day.",
    "",
    "Q: Site is behind Cloudflare. Why's everything blank?",
    "  → Scraper can't pass the JS challenge. Fill manually or wait for ScrapingBee.",
    "",
    "Q: Does an override ever get overwritten?",
    "  → No. Override wins forever, until you clear it (set to “— not set —”).",
    "",
    "Q: Pricing edits — where?",
    "  → HubSpot directly. Pricing fields never flow to Fluency or the editor.",
  ].map((t) => ({ text: t, options: { bullet: false, breakLine: true, paraSpaceAfter: 3 } })),
    { x: 7.1, y: 2.65, w: 5.4, h: 4.0, fontFace: BF, fontSize: 11, color: INK, valign: "top" });
  footer(s, 17);
}

// ════ SLIDE 18 — Operational mechanics + endpoints ════
{
  const s = pres.addSlide();
  lightHeader(s, "Operational reference", "Endpoints, files, and where to look when something breaks", 22);
  const sections = [
    ["KEY API ENDPOINTS", COPPER, [
      "POST /webhooks/clickup/property-brief             ClickUp ticket intake",
      "POST /api/internal/community-brief-capture-scan   Auto-PLE cron + bulk script",
      "GET  /api/accounts/property?company_id=X          Page render (X-Portal-Email)",
      "PATCH /api/accounts/property/field                Inline edit (X-Portal-Email)",
      "GET  /property-brief/approve/<token>              Approval portal UI",
      "PATCH /api/community-brief/<token>/field          Approval portal edits",
    ]],
    ["KEY FILES", SAGE, [
      "webhook-server/community_brief.py                 Field schema + write_field",
      "webhook-server/community_brief_capture.py         Auto-PLE + scope filter",
      "webhook-server/brief_ai_drafter.py                LLM scrape + extract",
      "webhook-server/property_brief.py                  ClickUp webhook orchestrator",
      "webhook-server/services/fluency_ingestion/        Daily tag-sync pipeline",
      "hubspot-cms/templates/accounts-detail.html        The /accounts editor template",
    ]],
    ["KEY ENV VARS (Render web service)", NAVY2, [
      "BRIEF_LLM_ENABLED          true | false  (kill switch for Path B)",
      "APT_IQ_FLOOR_PLAN_SHEET_URL                Apt IQ floor-plan CSV source",
      "CLICKUP_LIST_PROPERTY_BRIEF                Intake-list ID (901112045284)",
      "CLICKUP_WEBHOOK_SECRET                     ClickUp signing secret(s) — CSV",
      "HUBSPOT_QUOTE_TEMPLATE_ID                  472873408612 (Marketing Services IO)",
      "ANTHROPIC_API_KEY                          Sonnet API access",
    ]],
  ];
  let yy = 1.9;
  sections.forEach((sec, i) => {
    const h = 1.5;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: yy, w: 11.9, h, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: yy, w: 0.14, h, fill: { color: sec[1] } });
    s.addText(sec[0], { x: 0.95, y: yy + 0.1, w: 11, h: 0.32, fontFace: BF, fontSize: 12, bold: true,
      color: NAVY, charSpacing: 1, margin: 0 });
    s.addText(sec[2].map((r) => ({ text: r, options: { bullet: false, breakLine: true, paraSpaceAfter: 0 } })),
      { x: 0.95, y: yy + 0.42, w: 11.4, h: h - 0.5, fontFace: "Consolas", fontSize: 9.5, color: INK, valign: "top" });
    yy += h + 0.1;
  });
  footer(s, 18);
}

// ════ SLIDE 19 — Open work + dependencies ════
{
  const s = pres.addSlide();
  lightHeader(s, "Open work + dependencies", "What's parked, what's blocking, what's nice-to-have", 22);
  const cols = [
    [["ScrapingBee / ZenRows fallback", "$50/mo  ·  high ROI",
     "~80% of properties stuck behind CF/Akamai bot challenges. Wire a fallback path so the LLM gets real content for those too."],
    ["Stale-brief refresh cron", "weekly  ·  90-day TTL",
     "When a property rebrands or updates its site, the brief should re-LLM automatically. Force re-capture if older than 90d."],
    ["Slack digest", "5 min build",
     "End-of-daily-cron summary: captured, exists, errors, fields written. Visibility for the team."]],

    [["Reputation + Social Posting tier→product map", "blocked",
     "These two SKUs are tier-priced but have no single catalog product yet. Quotes can't include them — line items get dropped."],
    ["Website hosting catalog product", "blocked",
     "Same problem. Need product IDs wired so they can be quoted."],
    ["Empty-scrape short-circuit", "1 hour fix",
     "Daily cron currently calls Claude on blank scrapes ($0.02 wasted per CF site × 800 sites = $16/day if we re-force)."]],

    [["Customer Match (paused)", "spend ready",
     "Hashed-contact list to MCC audience for all 700+ Google Ads accounts. Most build done; audience creation + cron scheduling pending."],
    ["Hyly Convert-layer integration", "Q3",
     "Closed-loop attribution: visits → leads → leases. Connects the flywheel — better Fluency input → better Hyly output → better next input."],
    ["NinjaCat sunset (Feb 2026)", "in-flight",
     "Replace aggregated paid + SEO reports with direct API connectors + portal Loop view. Independent track."]],
  ];
  const xs = [0.7, 4.92, 9.14], cw = 3.95, cy = 1.9, ch = 4.85;
  cols.forEach((entries, i) => {
    const x = xs[i];
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: cw, h: ch, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: 0.1, h: ch, fill: { color: [COPPER, NAVY2, SAGE][i] } });
    let yy = cy + 0.2;
    entries.forEach((e) => {
      s.addText(e[0], { x: x + 0.25, y: yy, w: cw - 0.45, h: 0.32, fontFace: BF, fontSize: 12.5,
        bold: true, color: NAVY, margin: 0 });
      s.addText(e[1], { x: x + 0.25, y: yy + 0.32, w: cw - 0.45, h: 0.28, fontFace: BF, fontSize: 10,
        italic: true, color: [COPPER, NAVY2, SAGE][i], margin: 0 });
      s.addText(e[2], { x: x + 0.25, y: yy + 0.6, w: cw - 0.45, h: 0.85, fontFace: BF, fontSize: 10.5,
        color: INK, valign: "top", margin: 0 });
      yy += 1.55;
    });
  });
  footer(s, 19);
}

// ════ SLIDE 20 — Where to LOOK: the 4-database inspection map ════
{
  const s = pres.addSlide();
  lightHeader(s, "Concrete reference", "Where to look + how to inspect each layer", 22);
  // Left column header
  s.addText("WHERE EACH LAYER LIVES", { x: 0.7, y: 1.9, w: 5.9, h: 0.35, fontFace: BF, fontSize: 12,
    bold: true, color: COPPER, charSpacing: 1.5 });
  // Right column header
  s.addText("HOW TO QUERY", { x: 6.8, y: 1.9, w: 5.8, h: 0.35, fontFace: BF, fontSize: 12,
    bold: true, color: COPPER, charSpacing: 1.5 });

  const rows = [
    ["LAYER 1 — AptIQ (sources)", SAGE,
     "Daily CSV at the URL stored in APT_IQ_FLOOR_PLAN_SHEET_URL env var.\nShared Google Drive folder owned by AptIQ.",
     "curl \"$APT_IQ_FLOOR_PLAN_SHEET_URL\"  →  CSV stream\nFields: property_id, market_id, amenities, year_built, floor_plans, avg_rent, occupancy, …"],
    ["LAYER 2 — HubSpot (source of truth)", COPPER,
     "app.hubspot.com → Companies → pick a property → Properties tab.\nSearch the property list for “fluency_”.",
     "GET /crm/v3/objects/companies/{cid}?properties=fluency_voice_tier,fluency_amenities,fluency_taglines,…\n45 fluency_* properties + fluency_*_override mirrors."],
    ["LAYER 3 — HubDB (workflow store)", NAVY2,
     "app.hubspot.com → Marketing → Files → HubDB → rpm_property_briefs.\nTable id 281385088.",
     "GET /cms/v3/hubdb/tables/281385088/rows\nor use the find_by_ticket query: ?ticket_id__eq=auto:{cid}\nReturns: token, ticket_id, company_id, brief_markdown, status."],
    ["LAYER 4 — Google Sheet (Fluency reader)", COPPER,
     "docs.google.com/spreadsheets/d/{RPM_PIPELINE_SHEET_ID} → tab “rpm_property_tag_source”.\nSheet ID stored in the Render env.",
     "Open in browser; or use the Sheets API with service account credentials.\n22 columns; one row per property; keyed by account_id (= uuid)."],
  ];
  rows.forEach((r, i) => {
    const y = 2.3 + i * 1.1;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 11.9, h: 1.0, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y, w: 0.14, h: 1.0, fill: { color: r[1] } });
    s.addText(r[0], { x: 0.95, y: y + 0.08, w: 11.5, h: 0.3, fontFace: BF, fontSize: 12, bold: true,
      color: NAVY, charSpacing: 1, margin: 0 });
    s.addText(r[2], { x: 0.95, y: y + 0.4, w: 5.65, h: 0.55, fontFace: BF, fontSize: 10.5,
      color: INK, valign: "top", margin: 0 });
    s.addText(r[3], { x: 6.8, y: y + 0.4, w: 5.7, h: 0.55, fontFace: "Consolas", fontSize: 9.5,
      color: INK, valign: "top", margin: 0 });
  });
  footer(s, 20);
}

// ════ SLIDE 21 — Live Layer 2 sample: 17th Street Rentals ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 2 — Live example", "17th Street Rentals — actual HubSpot fluency_* values", 22);
  // Identity strip
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.85, w: 11.9, h: 0.78, fill: { color: NAVY }, shadow: sh() });
  s.addText("HUBSPOT COMPANY RECORD", { x: 0.95, y: 1.92, w: 4.0, h: 0.3, fontFace: BF, fontSize: 11,
    bold: true, color: COPPER_LT, charSpacing: 1.5 });
  s.addText("name: 17th Street Rentals  ·  company_id: 28669778583  ·  uuid: 28669778583 (Fluency join key)  ·  market: Atlanta",
    { x: 0.95, y: 2.25, w: 11.7, h: 0.32, fontFace: "Consolas", fontSize: 10.5, color: WHITE });

  // Tagged data
  const examples = [
    ["fluency_advertised_name_override",   '"17th Street Lofts"'],
    ["fluency_short_name_override",        '"17th Street Lofts"'],
    ["fluency_taglines",                   '"Live. Work. Play. / Loft Living / A modern masterpiece."'],
    ["fluency_brand_adjectives",           '"Modern · Vibrant · Thoughtful · Exceptional · Luxurious"'],
    ["fluency_differentiators",            '"Airbnb-friendly luxury apartments · Heart of Atlantic Station · …"'],
    ["fluency_property_amenities_override",'"Gated Community with Controlled Access · Covered Parking · Elevators · …"'],
    ["fluency_unit_features_override",     '"Hardwood Flooring · Vaulted Ceilings · Quartz Countertops · …"'],
    ["fluency_marketed_amenity_names_override",'"Gated Community with Controlled Access · Intercom Entry System · …"'],
    ["fluency_amenities_descriptions_override",'"17th Street Lofts offers interior landscapes that speak to the vibe of …"'],
    ["fluency_neighborhood_override",      '"Atlantic Station"'],
    ["fluency_landmarks_override",         '"Atlantic Station · Piedmont Park · Atlanta Botanical Garden · Zoo Atlanta"'],
    ["fluency_nearby_employers_override",  '"The Coca-Cola Company · Delta Air Lines · Home Depot · Georgia-Pacific · …"'],
    ["rpm_brief_status",                   '"pending_approval"'],
    ["rpm_brief_captured_at",              '"2026-06-02T21:57:47.065Z"'],
  ];
  examples.forEach((e, i) => {
    const y = 2.8 + i * 0.28;
    s.addText(e[0], { x: 0.9, y, w: 4.0, h: 0.28, fontFace: "Consolas", fontSize: 10, bold: true,
      color: NAVY, margin: 0 });
    s.addText(e[1], { x: 4.95, y, w: 7.7, h: 0.28, fontFace: "Consolas", fontSize: 10, color: INK, margin: 0 });
  });
  s.addText("All values are LLM-extracted from 17thstreetlofts.com (homepage + /amenities + /floorplans + /neighborhood). All can be overridden on /accounts.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 10.5, italic: true, color: MUTE, align: "center" });
  footer(s, 21);
}

// ════ SLIDE 22 — Layer 3 sample: HubDB rpm_property_briefs ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 3 — Live example", "HubDB rpm_property_briefs — what a brief row looks like", 22);
  // Schema table
  s.addText("SCHEMA  ·  table id 281385088", { x: 0.7, y: 1.95, w: 6, h: 0.3, fontFace: BF, fontSize: 11.5, bold: true, color: COPPER, charSpacing: 1.2 });
  const schema = [
    ["token",            "varchar(48)",  "Unguessable per-brief secret. URL path component."],
    ["ticket_id",        "varchar(64)",  "ClickUp ticket id, OR auto:<cid> for cron-captured briefs."],
    ["company_id",       "varchar(20)",  "HubSpot hs_object_id of the property."],
    ["deal_id",          "varchar(20)",  "HubSpot deal id (null for auto-PLE captures)."],
    ["submitter_email",  "varchar(120)", "Who filed the ticket / kicked off capture."],
    ["rm_email",         "varchar(120)", "Where the quote should be sent (Path A)."],
    ["brief_markdown",   "text",         "Full markdown brief — Overview / Target / Voice / …"],
    ["status",           "enum",         "pending_approval | approved | needs_edits | rejected."],
    ["created_at_ms",    "bigint",       "Epoch milliseconds at capture time."],
  ];
  schema.forEach((r, i) => {
    const y = 2.35 + i * 0.34;
    s.addText(r[0], { x: 0.7, y, w: 2.5, h: 0.3, fontFace: "Consolas", fontSize: 10.5, bold: true, color: NAVY });
    s.addText(r[1], { x: 3.2, y, w: 1.9, h: 0.3, fontFace: "Consolas", fontSize: 10, color: SAGE });
    s.addText(r[2], { x: 5.2, y, w: 7.4, h: 0.3, fontFace: BF, fontSize: 10.5, color: INK });
  });

  // Sample row
  s.addText("SAMPLE ROW  ·  17th Street Rentals", { x: 0.7, y: 5.45, w: 8, h: 0.3, fontFace: BF, fontSize: 11.5, bold: true, color: COPPER, charSpacing: 1.2 });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 5.8, w: 11.9, h: 1.0, fill: { color: "1A2530" } });
  s.addText([
    "token            = \"Kzat4qCC9pXMRr0Z1tBwS7VgVqr…\"",
    "ticket_id        = \"auto:28669778583\"",
    "company_id       = \"28669778583\"",
    "status           = \"pending_approval\"",
    "approval URL     = https://rpm-portal-server.onrender.com/property-brief/approve/Kzat4qCC9p…",
  ].map((r) => ({ text: r, options: { bullet: false, breakLine: true } })),
    { x: 0.9, y: 5.85, w: 11.5, h: 0.9, fontFace: "Consolas", fontSize: 9.5, color: "DCE6F5", valign: "top" });
  footer(s, 22);
}

// ════ SLIDE 23 — Layer 4 sample: the Sheet column-by-column ════
{
  const s = pres.addSlide();
  lightHeader(s, "Layer 4 — Live example", "The Sheet Fluency reads — 22 columns, one row per property", 22);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.85, w: 11.9, h: 0.65, fill: { color: NAVY }, shadow: sh() });
  s.addText("rpm_property_tag_source  ·  tab in “RPM Property Tag Source” Google Sheet",
    { x: 0.9, y: 1.85, w: 11.5, h: 0.65, valign: "middle", fontFace: "Consolas", fontSize: 12, bold: true, color: COPPER_LT });

  // Caption moved above the cards so the cards have full bottom-of-slide room.
  s.addText("Pricing (avg_rent, concessions), internal fields (lease_signal_text), and audience tags are EXCLUDED by enum.",
    { x: 0.7, y: 2.6, w: 12, h: 0.3, fontFace: BF, fontSize: 10.5, italic: true, color: MUTE, align: "center" });

  // 3 column groups, packed DATA TAGS into 3 cols per row to fit on slide.
  const groups = [
    ["IDENTITY  (cols 1-5)", COPPER, [
      "1.  account_id          = uuid  ★ FLUENCY JOIN KEY",
      "2.  hubspot_company_id  = hs_object_id (internal ref)",
      "3.  account_name        = property name",
      "4.  account_market      = RPM Market",
      "5.  account_state       = US state",
    ]],
    ["DATA TAGS  (cols 6-20)  →  Fluency reads these", SAGE, [
      "6.  data:voice_tier             7. data:lifecycle_state         8. data:unit_noun",
      "9.  data:amenities             10. data:marketed_amenity_names  11. data:amenities_descriptions",
      "12. data:floor_plans           13. data:year_built              14. data:year_renovated",
      "15. data:must_include          16. data:forbidden_phrases       17. data:neighborhood",
      "18. data:landmarks             19. data:nearby_employers        20. data:competitors",
    ]],
    ["METADATA  (cols 21-22)", NAVY2, [
      "21. hash             = per-row content hash (for diff)",
      "22. last_updated_at  = ISO8601 UTC of last write",
    ]],
  ];
  let yy = 3.05;
  groups.forEach((g) => {
    const lineCount = g[2].length;
    const h = 0.4 + lineCount * 0.22;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: yy, w: 11.9, h, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: yy, w: 0.12, h, fill: { color: g[1] } });
    s.addText(g[0], { x: 0.95, y: yy + 0.04, w: 11.4, h: 0.28, fontFace: BF, fontSize: 11, bold: true,
      color: NAVY, charSpacing: 1.2, margin: 0 });
    s.addText(g[2].map((r) => ({ text: r, options: { bullet: false, breakLine: true, paraSpaceAfter: 0 } })),
      { x: 0.95, y: yy + 0.34, w: 11.4, h: h - 0.4, fontFace: "Consolas", fontSize: 9.5, color: INK, valign: "top" });
    yy += h + 0.1;
  });
  footer(s, 23);
}

// ════ SLIDE 24 — Calling it into Fluency ════
{
  const s = pres.addSlide();
  lightHeader(s, "Calling it into Fluency", "How the Sheet becomes ad-copy fields in Fluency", 22);
  // Top-level: the connection
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 1.85, w: 11.9, h: 0.7, fill: { color: NAVY }, shadow: sh() });
  s.addText("FLUENCY CONNECTION", { x: 0.95, y: 1.93, w: 5, h: 0.3, fontFace: BF, fontSize: 11.5, bold: true, color: COPPER_LT, charSpacing: 1.5 });
  s.addText("Fluency Settings → Data Sources → Add Google Sheet → paste the RPM Property Tag Source URL → tab “rpm_property_tag_source” → join key “account_id”.",
    { x: 0.95, y: 2.21, w: 11.6, h: 0.32, fontFace: BF, fontSize: 11.5, color: WHITE });

  // 4 step cards
  const steps = [
    ["1. SHEET → FLUENCY", COPPER,
     ["Fluency polls the Sheet on its own daily cadence.",
      "Reads every row keyed by account_id (= our uuid).",
      "Joins to Fluency's account record by matching uuid.",
      "No code on our side — it's a UI-level connection."]],
    ["2. COLUMN → TAG FIELD", SAGE,
     ["Each data: column maps to a Fluency tag field.",
      "data:amenities          → amenities tag bucket",
      "data:voice_tier         → tone selector (value/standard/lifestyle/luxury)",
      "data:neighborhood       → local-relevance signal",
      "data:taglines           → headline pool"]],
    ["3. TAG → AD COPY", NAVY2,
     ["Fluency generates Responsive Search / PMax / Display assets.",
      "Picks variants per tag bucket (amenities → bulleted callouts).",
      "Headlines pull from taglines + advertised_name + selling_points.",
      "Excludes phrases from forbidden_phrases at generation time."]],
    ["4. AD → ATTRIBUTION", COPPER,
     ["Generated ads ship to Google Ads / Meta / etc.",
      "Attribution returns via Hyly Convert layer (Phase 4).",
      "Better attribution → better next-cycle voice_tier / lifecycle_state guesses.",
      "The flywheel: tighter brief → sharper ads → better signal back."]],
  ];
  steps.forEach((st, i) => {
    const x = 0.7 + (i % 2) * 6.05, y = 2.8 + Math.floor(i / 2) * 1.9, w = 5.85, h = 1.75;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.45, fill: { color: st[1] } });
    s.addText(st[0], { x: x + 0.2, y, w: w - 0.4, h: 0.45, valign: "middle", fontFace: BF,
      fontSize: 12, bold: true, color: WHITE, charSpacing: 1 });
    s.addText(st[2].map((r) => ({ text: r, options: { bullet: { code: "2022", indent: 12 }, breakLine: true, paraSpaceAfter: 3 } })),
      { x: x + 0.25, y: y + 0.55, w: w - 0.5, h: h - 0.6, fontFace: BF, fontSize: 10.5, color: INK, valign: "top" });
  });
  s.addText("Net: edit a fluency_*_override on /accounts → 24h later it's in the Sheet → 24h later it's in live ad copy.",
    { x: 0.7, y: 6.85, w: 12, h: 0.3, fontFace: BF, fontSize: 11, bold: true, italic: true, color: NAVY, align: "center" });
  footer(s, 24);
}

// ════ SLIDE 25 — Takeaway (dark) ════
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.28, fill: { color: COPPER } });
  s.addText("THE TAKEAWAY", { x: 0.9, y: 1.3, w: 11, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
    color: COPPER, charSpacing: 3, margin: 0 });
  s.addText("One source of truth. Four layers. A daily sync. The override always wins.",
    { x: 0.9, y: 1.85, w: 11.7, h: 2.0, fontFace: HF, fontSize: 32, bold: true, color: WHITE, margin: 0 });
  s.addText([
    { text: "The Community Brief is the HUMAN side — 12 sections of fluency_*_override on the HubSpot company.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "The Fluency Tag Pipeline is the AUTOMATED side — daily sync from HubSpot to the “RPM Property Tag Source” Google Sheet.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "HubDB stores the BRIEFS THEMSELVES — markdown documents + AI drafts + SEO content briefs — separate from the live field-level data.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "Fluency only reads the Google Sheet. Override > resolved > pipeline default. Pricing + internal fields never flow.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5", breakLine: true, paraSpaceAfter: 10 } },
    { text: "Rollout is phased: bulk LLM populate this week → scraping service in 30 days → closed-loop attribution in 90.", options: { bullet: { code: "2022", indent: 14 }, color: "DCE6F5" } },
  ], { x: 0.9, y: 4.0, w: 11.6, h: 2.7, fontFace: BF, fontSize: 14, valign: "top" });
  s.addText("Full detail: docs/CLIENT_BRIEF_SYSTEM.md  ·  docs/FLUENCY_PIPELINE.md  ·  /accounts/property — try it yourself",
    { x: 0.9, y: 6.85, w: 11.6, h: 0.4, fontFace: BF, fontSize: 12, color: "9AA7B4", margin: 0 });
}

const out = path.join(os.homedir(), "Downloads", "RPM_Community_Brief_to_Fluency.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("WROTE " + out));
