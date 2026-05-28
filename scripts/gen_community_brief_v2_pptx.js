/* Generates a coworker briefing deck for the Community Brief v2 rework —
 * what changed + how the whole flow works. RPM brand, numbered-step motif.
 * Run: node scripts/gen_community_brief_v2_pptx.js
 * Output: ~/Downloads/RPM_Community_Brief_v2.pptx
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
pres.title = "RPM Community Brief v2";
const W = 13.33, H = 7.5;

const sh = () => ({ type: "outer", color: "000000", blur: 7, offset: 3, angle: 135, opacity: 0.13 });

function footer(slide, n) {
  slide.addText("RPM Community Brief — what changed & how it flows",
    { x: 0.6, y: H - 0.42, w: 9, h: 0.3, fontFace: BF, fontSize: 9, color: MUTE, align: "left", margin: 0 });
  slide.addText(String(n), { x: W - 1.0, y: H - 0.42, w: 0.5, h: 0.3,
    fontFace: BF, fontSize: 9, color: MUTE, align: "right", margin: 0 });
}

function lightHeader(s, kicker, title, titleSize) {
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addText(kicker.toUpperCase(), { x: 0.7, y: 0.55, w: 12, h: 0.4, fontFace: BF, fontSize: 14,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 0.7, y: 0.98, w: 12.2, h: 0.95, fontFace: HF, fontSize: titleSize || 27,
    bold: true, color: NAVY, margin: 0 });
}

// Two-line entry inside a panel: bold name + gray sub.
function panelEntries(s, x, y, w, entries, accent) {
  let yy = y;
  entries.forEach((e) => {
    s.addText(e[0], { x: x + 0.28, y: yy, w: w - 0.5, h: 0.34, fontFace: BF, fontSize: 14,
      bold: true, color: NAVY, margin: 0 });
    s.addText(e[1], { x: x + 0.28, y: yy + 0.33, w: w - 0.5, h: 0.42, fontFace: BF, fontSize: 11,
      color: MUTE, margin: 0, valign: "top" });
    yy += 0.98;
  });
}

// Step slide: numbered circle + title + bullets, card on the right.
function stepSlide(n, posNum, kicker, title, whatLines, cardTitle, cardRows, cardAccent) {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 0.22, h: H, fill: { color: COPPER } });
  s.addShape(pres.shapes.OVAL, { x: 0.7, y: 0.7, w: 1.15, h: 1.15, fill: { color: NAVY }, shadow: sh() });
  s.addText(String(n), { x: 0.7, y: 0.7, w: 1.15, h: 1.15, align: "center", valign: "middle",
    fontFace: HF, fontSize: 40, bold: true, color: COPPER_LT, margin: 0 });
  s.addText(kicker.toUpperCase(), { x: 2.1, y: 0.72, w: 6.7, h: 0.35, fontFace: BF, fontSize: 13,
    bold: true, color: COPPER, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 2.1, y: 1.06, w: 6.9, h: 0.95, fontFace: HF, fontSize: 27, bold: true,
    color: NAVY, margin: 0 });
  s.addText(whatLines.map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 },
    breakLine: true, paraSpaceAfter: 9 } })),
    { x: 2.15, y: 2.3, w: 5.0, h: 4.3, fontFace: BF, fontSize: 15, color: INK, valign: "top" });
  const cardX = 7.7, cardY = 1.95, cardW = 5.0, cardH = 4.5;
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: cardH,
    fill: { color: CARD }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: cardX, y: cardY, w: cardW, h: 0.62, fill: { color: cardAccent || NAVY2 } });
  s.addText(cardTitle, { x: cardX + 0.25, y: cardY, w: cardW - 0.5, h: 0.62, valign: "middle",
    fontFace: BF, fontSize: 13, bold: true, color: WHITE, charSpacing: 1, margin: 0 });
  s.addText(cardRows.map((r) => ({ text: r, options: { bullet: false, breakLine: true, paraSpaceAfter: 7 } })),
    { x: cardX + 0.3, y: cardY + 0.85, w: cardW - 0.6, h: cardH - 1.1, fontFace: "Consolas",
      fontSize: 12, color: INK, valign: "middle" });
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
  s.addText("The Community Brief, Reworked", { x: 0.9, y: 2.1, w: 11.7, h: 1.4,
    fontFace: HF, fontSize: 50, bold: true, color: WHITE, margin: 0 });
  s.addText("From scattered intake to a living, self-improving property record",
    { x: 0.9, y: 3.65, w: 11.5, h: 0.7, fontFace: BF, fontSize: 22, color: COPPER_LT, margin: 0 });
  s.addText("Team briefing  ·  Digital + Branding & Creative", { x: 0.9, y: 6.4, w: 11, h: 0.4,
    fontFace: BF, fontSize: 13, color: "9AA7B4", margin: 0 });
}

// ════ SLIDE 2 — The flywheel (light) ════
{
  const s = pres.addSlide();
  lightHeader(s, "The big idea", "One brief per community — better in, better out", 26);
  const stages = [
    ["Capture", "Auto + human inputs", COPPER],
    ["Review & Edit", "Property Marketing steers", SAGE],
    ["Publish", "Lands in HubSpot", NAVY2],
    ["Better campaigns", "Fluency builds on-brand", COPPER],
  ];
  const cw = 2.75, cy = 2.6, ch = 1.7;
  const xs = [0.7, 3.75, 6.8, 9.85];   // even 0.3in gaps between cards
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
      const ax = x + cw;            // card right edge; 0.3in gap to next card
      s.addShape(pres.shapes.LINE, { x: ax + 0.04, y: cy + ch / 2, w: 0.22, h: 0,
        line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
    }
  });
  // Feed-back arrow (flywheel close) beneath the row, pointing left.
  s.addShape(pres.shapes.LINE, { x: 0.95, y: 5.05, w: 11.2, h: 0,
    line: { color: SAGE, width: 2.5, beginArrowType: "triangle" } });
  s.addText("The loop closes — every cycle feeds the next: more & better data in  →  sharper campaigns  →  more signal back.",
    { x: 0.7, y: 5.35, w: 12, h: 0.7, fontFace: BF, fontSize: 14, italic: true, color: NAVY, align: "center", margin: 0 });
  footer(s, 2);
}

// ════ SLIDE 3 — Before → After (light, two columns) ════
{
  const s = pres.addSlide();
  lightHeader(s, "What changed", "From a flat intake form to a living record", 27);
  // Before card
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.0, w: 5.9, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.0, w: 5.9, h: 0.6, fill: { color: MUTE } });
  s.addText("BEFORE", { x: 0.95, y: 2.0, w: 5.4, h: 0.6, valign: "middle", fontFace: BF, fontSize: 14,
    bold: true, color: WHITE, charSpacing: 2, margin: 0 });
  s.addText([
    "One amenities blob",
    "Floor plans = bedroom buckets only",
    "Neighborhood = one flat field",
    "No tracking numbers / UTMs",
    "No place for pitch decks or RFPs",
    "Brief = prose; not editable in HubSpot",
    "Kicked off only by a ClickUp ticket",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 9 } })),
    { x: 0.95, y: 2.85, w: 5.4, h: 3.6, fontFace: BF, fontSize: 14.5, color: INK, valign: "top" });
  // Now card
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.0, w: 5.7, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.0, w: 5.7, h: 0.6, fill: { color: COPPER } });
  s.addText("NOW", { x: 7.15, y: 2.0, w: 5.2, h: 0.6, valign: "middle", fontFace: BF, fontSize: 14,
    bold: true, color: WHITE, charSpacing: 2, margin: 0 });
  s.addText([
    "Property Amenities + In-Unit Features",
    "Structured floor plans from Apt IQ (name/beds/baths/sq ft)",
    "Geography: In / Near / Close To / Highlights",
    "Tracking # + UTM for all 13 sources",
    "Documents: pitch decks, RFPs, brand guides",
    "Editable on BOTH the link and /accounts",
    "Auto-captures when a property goes RPM Managed",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 8 } })),
    { x: 7.15, y: 2.85, w: 5.2, h: 3.6, fontFace: BF, fontSize: 14.5, color: INK, valign: "top" });
  footer(s, 3);
}

// ════ SLIDE 4 — The structure (light, 3 panels) ════
{
  const s = pres.addSlide();
  lightHeader(s, "The structure", "Twelve sections, one source of truth", 27);
  const cols = [
    [["Voice & Positioning", "Tier, unit noun, names, former name"],
     ["Brand & Story", "Taglines, adjectives, differentiators, residents"],
     ["Lifecycle", "Lease-up / stabilized / renovated"],
     ["Inventory", "Structured floor plans (Apt IQ)"]],
    [["Amenities", "Property-level + In-Unit, split out"],
     ["Geography", "In / Near / Close To / Highlights"],
     ["Competitors", "Same-market rent peers"],
     ["Strategy & Goals", "Goals, initiatives, events, partnerships"]],
    [["Operations & Tech", "PMS, CMS, CRM, budget — internal"],
     ["Guardrails", "Forbidden phrases, do-not-target"],
     ["Tracking & Attribution", "Number + UTM, 13 sources"],
     ["Documents", "Pitch decks, RFPs, brand guides"]],
  ];
  const xs = [0.7, 4.92, 9.14], cw = 3.95, cy = 2.0, ch = 4.55;
  cols.forEach((entries, i) => {
    const x = xs[i];
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: cw, h: ch, fill: { color: CARD },
      line: { color: CARDLINE, width: 1 }, shadow: sh() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: cy, w: 0.1, h: ch, fill: { color: [COPPER, SAGE, NAVY2][i] } });
    panelEntries(s, x, cy + 0.3, cw, entries);
  });
  footer(s, 4);
}

// ════ SLIDE 5 — End-to-end flow (hero diagram, light) ════
{
  const s = pres.addSlide();
  lightHeader(s, "How it flows", "From RPM-Managed to live ads, step by step", 26);
  // Combined inputs box, centered above stage 2 (AI Capture), single feed arrow.
  const srcY = 2.05, capCenter = 0.55 + 1 * (2.3 + 0.2) + 2.3 / 2; // stage-2 center x
  const ibW = 4.2, ibX = capCenter - ibW / 2;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: ibX, y: srcY, w: ibW, h: 0.92, rectRadius: 0.08,
    fill: { color: WHITE }, line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addText("INPUTS", { x: ibX + 0.2, y: srcY + 0.1, w: ibW - 0.4, h: 0.3, fontFace: BF, fontSize: 11,
    bold: true, color: COPPER, charSpacing: 1.5, margin: 0 });
  s.addText("Apt IQ — facts & floor plans   ·   Website — voice & neighborhood",
    { x: ibX + 0.2, y: srcY + 0.42, w: ibW - 0.4, h: 0.42, fontFace: BF, fontSize: 11.5,
      bold: true, color: NAVY, margin: 0, valign: "top" });
  // 5-stage numbered lane
  const stages = [
    ["RPM Managed", "the trigger"],
    ["AI Capture", "scrape + LLM"],
    ["Link on company", "pending approval"],
    ["Review & Edit", "override wins"],
    ["Publish", "/accounts + Fluency"],
  ];
  const laneY = 3.95, bw = 2.3, bh = 1.5, gap = 0.2;
  const lx = (i) => 0.55 + i * (bw + gap);   // 0.55, 3.05, 5.55, 8.05, 10.55
  stages.forEach((st, i) => {
    const x = lx(i);
    s.addShape(pres.shapes.RECTANGLE, { x, y: laneY, w: bw, h: bh, fill: { color: NAVY }, shadow: sh() });
    s.addText(String(i + 1), { x: x + 0.12, y: laneY + 0.08, w: 0.6, h: 0.4, fontFace: HF, fontSize: 18,
      bold: true, color: COPPER_LT, margin: 0 });
    s.addText(st[0], { x: x + 0.12, y: laneY + 0.52, w: bw - 0.24, h: 0.5, fontFace: BF, fontSize: 13,
      bold: true, color: WHITE, margin: 0, valign: "top" });
    s.addText(st[1], { x: x + 0.12, y: laneY + 1.04, w: bw - 0.24, h: 0.35, fontFace: BF, fontSize: 10.5,
      color: "9AA7B4", margin: 0 });
    if (i < 4) {
      const ax = x + bw;  // box right edge; gap is 0.2
      s.addShape(pres.shapes.LINE, { x: ax, y: laneY + bh / 2, w: gap, h: 0,
        line: { color: COPPER, width: 2.5, endArrowType: "triangle" } });
    }
  });
  // Single straight feed: inputs box → AI Capture (stage 2) top.
  s.addShape(pres.shapes.LINE, { x: capCenter, y: srcY + 0.92, w: 0, h: laneY - (srcY + 0.92),
    line: { color: SAGE, width: 2.5, endArrowType: "triangle" } });
  s.addText("Override-wins throughout — a human edit always beats the auto value. Fluency re-reads daily.",
    { x: 0.7, y: 5.95, w: 12, h: 0.5, fontFace: BF, fontSize: 13, italic: true, color: NAVY, align: "center", margin: 0 });
  footer(s, 5);
}

// ════ SLIDES 6-8 — the mechanics (step slides) ════
stepSlide(1, 6, "It starts itself", "Auto-capture on RPM Managed",
  [ "When a property's PLE status flips to RPM Managed, a daily cron runs the AI capture.",
    "It scrapes the property site + drafts the brief — capturing what it can, automatically.",
    "The approval link is written ONTO the company record so it never gets lost." ],
  "AUTO-CAPTURE", [
    "trigger: plestatus",
    "        = RPM Managed",
    "",
    "→ scrape + LLM draft",
    "→ brief + token",
    "→ rpm_brief_approval_url",
    "  on the company",
    "→ status: pending_approval",
  ], COPPER);

stepSlide(2, 7, "Humans steer it", "Review, edit, publish",
  [ "A reviewer opens the link, confirms what's right and edits what isn't — inline.",
    "Every edit writes an override; the override always wins over the auto value.",
    "“Looks good” publishes the brief to the HubSpot /accounts/property side." ],
  "OVERRIDE > AUTO", [
    "edit any field",
    "  → fluency_*_override",
    "",
    "approve",
    "  → status: approved",
    "  → publishes to",
    "    /accounts/property",
    "editable in BOTH places",
  ], SAGE);

stepSlide(3, 8, "Apt IQ fills the facts", "Floor plans + the 30-day retry",
  [ "Structured floor plans (name, beds, baths, sq ft) come from Apt IQ's floor_plan report.",
    "Matching is an EXACT property-ID match — there is no fuzzy match.",
    "If a property is managed before its ID resolves, we retry the match for ~30 days." ],
  "APT IQ", [
    "floor_plan report",
    "  name/beds/baths/sqft",
    "",
    "match = exact ID",
    "  (no fuzzy match)",
    "retry ≤ 30 days",
    "  aptiq_match_status:",
    "  pending → matched / failed",
  ], NAVY2);

// ════ SLIDE 9 — Tracking & attribution (light) ════
{
  const s = pres.addSlide();
  lightHeader(s, "Attribution", "A tracking number + UTM for every source", 26);
  const sources = [
    "Brochure/Flyer", "Bandit Signs", "Yelp", "Zillow", "Apple Maps", "Banner", "Corporate Website",
    "CoStar/Apartments.com", "Google Business Profile/Maps", "Google Paid Search/PPC",
    "Property Website", "Social Ads", "Social Posting",
  ];
  // chips in 3 balanced columns (5 / 4 / 4)
  const colW = 3.95, xs = [0.7, 4.92, 9.14];
  sources.forEach((src, i) => {
    let col, row;
    if (i < 5) { col = 0; row = i; }
    else if (i < 9) { col = 1; row = i - 5; }
    else { col = 2; row = i - 9; }
    const x = xs[col], y = 2.15 + row * 0.62;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: colW - 0.2, h: 0.5, rectRadius: 0.06,
      fill: { color: WHITE }, line: { color: CARDLINE, width: 1 } });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.08, h: 0.5, fill: { color: COPPER } });
    s.addText(src, { x: x + 0.22, y, w: colW - 0.45, h: 0.5, valign: "middle", fontFace: BF,
      fontSize: 12.5, bold: true, color: NAVY, margin: 0 });
  });
  s.addText("Each row holds a call-tracking number + a UTM string (sensible defaults pre-filled). " +
    "That's closed-loop attribution across paid AND organic — Property Marketing fills the numbers once.",
    { x: 0.7, y: 5.55, w: 12, h: 0.9, fontFace: BF, fontSize: 14, italic: true, color: NAVY, valign: "top", margin: 0 });
  footer(s, 9);
}

// ════ SLIDE 10 — Fair Housing guardrails (light, callout) ════
{
  const s = pres.addSlide();
  lightHeader(s, "Guardrails", "Compliant by construction", 27);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.05, w: 11.9, h: 4.45, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.05, w: 11.9, h: 0.62, fill: { color: SAGE } });
  s.addText("FAIR HOUSING — BUILT IN", { x: 0.95, y: 2.05, w: 11.4, h: 0.62, valign: "middle",
    fontFace: BF, fontSize: 14, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "Pricing never reaches Fluency — avg rent, concessions, and floor-plan rent stay in HubSpot only.",
    "Sensitive / operational fields (budget, the “typical resident”, PMS, CMS, CRM) are flagged INTERNAL — stored and editable, but NEVER fed into ad-copy generation.",
    "“Things NOT to say” lets you hard-exclude any phrase from copy.",
    "“Neighborhoods NOT to target” steers keyword/copy targeting away from sensitive areas — without geo-fencing.",
    "The capture + preview prompts forbid demographic targeting language by construction.",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 13 } })),
    { x: 1.0, y: 2.95, w: 11.3, h: 3.4, fontFace: BF, fontSize: 15.5, color: INK, valign: "top" });
  footer(s, 10);
}

// ════ SLIDE 11 — Status & go-live (light, two columns) ════
{
  const s = pres.addSlide();
  lightHeader(s, "Where it stands", "Built & tested — and what's left to flip on", 26);
  // Live card
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.0, w: 5.9, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.7, y: 2.0, w: 5.9, h: 0.6, fill: { color: SAGE } });
  s.addText("LIVE & TESTED", { x: 0.95, y: 2.0, w: 5.4, h: 0.6, valign: "middle", fontFace: BF,
    fontSize: 14, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "New data model + all questionnaire fields",
    "Auto-capture cron + 30-day Apt IQ retry",
    "Structured floor plans from Apt IQ",
    "Editable approval portal + /accounts endpoints",
    "ClickUp attestation gate",
    "112 automated tests passing",
  ].map((t) => ({ text: t, options: { bullet: { code: "2713", indent: 14 }, breakLine: true, paraSpaceAfter: 10 } })),
    { x: 0.95, y: 2.85, w: 5.4, h: 3.6, fontFace: BF, fontSize: 14.5, color: INK, valign: "top" });
  // Go-live card
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.0, w: 5.7, h: 4.6, fill: { color: CARD },
    line: { color: CARDLINE, width: 1 }, shadow: sh() });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.9, y: 2.0, w: 5.7, h: 0.6, fill: { color: COPPER } });
  s.addText("TO FLIP ON", { x: 7.15, y: 2.0, w: 5.2, h: 0.6, valign: "middle", fontFace: BF,
    fontSize: 14, bold: true, color: WHITE, charSpacing: 1.5, margin: 0 });
  s.addText([
    "Set Apt IQ floor_plan report URL (Render env)",
    "Run the new properties migration",
    "Schedule the daily capture cron",
    "Create the ClickUp attestation checkbox",
    "Wire the /accounts page front-end (last piece)",
  ].map((t) => ({ text: t, options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: 12 } })),
    { x: 7.15, y: 2.85, w: 5.2, h: 3.6, fontFace: BF, fontSize: 14.5, color: INK, valign: "top" });
  footer(s, 11);
}

// ════ SLIDE 12 — Takeaway (dark) ════
{
  const s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: W, h: 0.28, fill: { color: COPPER } });
  s.addText("THE TAKEAWAY", { x: 0.9, y: 1.35, w: 11, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
    color: COPPER, charSpacing: 3, margin: 0 });
  s.addText("Better inputs. Better campaigns. A brief that never goes stale.",
    { x: 0.9, y: 1.95, w: 11.6, h: 2.0, fontFace: HF, fontSize: 36, bold: true, color: WHITE, margin: 0 });
  s.addText([
    { text: "Auto-captured the moment a property goes RPM Managed — then steered by Property Marketing.", options: { bullet: { code: "2022", indent: 14 }, color: "E6EDF7", breakLine: true, paraSpaceAfter: 11 } },
    { text: "Override-wins everywhere; editable on the approval link AND the HubSpot /accounts page.", options: { bullet: { code: "2022", indent: 14 }, color: "E6EDF7", breakLine: true, paraSpaceAfter: 11 } },
    { text: "Fair-Housing safe by construction; feeds Fluency on-brand, compliant ads daily.", options: { bullet: { code: "2022", indent: 14 }, color: "E6EDF7", breakLine: true, paraSpaceAfter: 11 } },
    { text: "The flywheel: every cycle of input makes the next campaign sharper.", options: { bullet: { code: "2022", indent: 14 }, color: "E6EDF7" } },
  ], { x: 0.9, y: 4.2, w: 11.6, h: 2.4, fontFace: BF, fontSize: 16.5, valign: "top" });
  s.addText("Full detail: docs/CLIENT_BRIEF_SYSTEM.md  ·  “Community Brief v2”",
    { x: 0.9, y: 6.85, w: 11.6, h: 0.4, fontFace: BF, fontSize: 12, color: "9AA7B4", margin: 0 });
}

const out = path.join(os.homedir(), "Downloads", "RPM_Community_Brief_v2.pptx");
pres.writeFile({ fileName: out }).then(() => console.log("WROTE " + out));
