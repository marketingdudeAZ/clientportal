/* Generates the Client Brief System review doc as a .docx for the
 * branding/creative peer to comment on. Run: node scripts/gen_client_brief_docx.js
 * Output: ~/Downloads/Client_Brief_System_Review.docx
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  LevelFormat, PageBreak,
} = require("docx");

const NAVY = "1F3A5F", GOLD = "C8964E", GREY = "57534E", RED = "C0392B";
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const cellBorders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function P(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after ?? 120, before: opts.before ?? 0 },
    children: [new TextRun({ text, bold: !!opts.bold, italics: !!opts.italics,
      color: opts.color, size: opts.size ?? 21 })],
    ...(opts.bullet ? { numbering: { reference: "bullets", level: 0 } } : {}),
    ...(opts.num ? { numbering: { reference: "numbers", level: 0 } } : {}),
  });
}
function H1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text })] });
}
function H2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text })] });
}
function mono(text) {
  return new Paragraph({
    spacing: { after: 140 },
    shading: { fill: "F4F4F2", type: ShadingType.CLEAR },
    children: [new TextRun({ text, font: "Courier New", size: 18 })],
  });
}
function tbl(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const mk = (txt, head) => new TableCell({
    borders: cellBorders, margins: cellMargins,
    width: { size: widths[0], type: WidthType.DXA },
    shading: head ? { fill: NAVY, type: ShadingType.CLEAR } : undefined,
    children: [new Paragraph({ children: [new TextRun({
      text: txt, bold: head, color: head ? "FFFFFF" : undefined, size: 19 })] })],
  });
  const rowOf = (arr, head) => new TableRow({
    children: arr.map((c, i) => new TableCell({
      borders: cellBorders, margins: cellMargins,
      width: { size: widths[i], type: WidthType.DXA },
      shading: head ? { fill: NAVY, type: ShadingType.CLEAR } : undefined,
      children: [new Paragraph({ children: [new TextRun({
        text: String(c), bold: head, color: head ? "FFFFFF" : undefined,
        size: 19 })] })],
    })),
  });
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: widths,
    rows: [rowOf(headers, true), ...rows.map((r) => rowOf(r, false))],
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal",
        quickFormat: true,
        run: { size: 30, bold: true, color: NAVY, font: "Arial" },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal",
        quickFormat: true,
        run: { size: 24, bold: true, color: GREY, font: "Arial" },
        paragraph: { spacing: { before: 220, after: 120 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET,
        text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 540, hanging: 280 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL,
        text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 540, hanging: 280 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1100, right: 1100, bottom: 1100, left: 1100 } } },
    children: [
      new Paragraph({ spacing: { after: 80 },
        children: [new TextRun({ text: "RPM LIVING", bold: true,
          color: GOLD, size: 22 })] }),
      new Paragraph({ spacing: { after: 60 },
        children: [new TextRun({ text: "The Client Brief System",
          bold: true, size: 44, color: NAVY })] }),
      new Paragraph({ spacing: { after: 240 },
        children: [new TextRun({ text: "How it works today — for Branding & Creative + Digital review",
          size: 22, color: GREY })] }),
      tbl(["", ""], [
        ["Owner", "Kyle Shipp — Digital Products & Services"],
        ["For review by", "Branding & Creative lead + Digital team"],
        ["Purpose", "Confirm the brief captures what both teams need; close any Fair Housing gaps before scaling"],
        ["Last updated", "2026-05-17"],
      ], [2400, 7000]),
      new Paragraph({ children: [new PageBreak()] }),

      H1("1. What “the brief” actually is (it’s two things)"),
      P("The word “brief” covers two related but distinct artifacts. Both describe a property’s marketing identity; they serve different consumers."),
      H2("A. The Property Brief (narrative)"),
      P("An AI-generated ~1-page marketing brief. Sections: Property Overview, Target Audience, Voice & Tone, Differentiators, Channel Strategy (one paragraph per purchased channel), Success Metrics.", { bullet: true }),
      P("Consumer: humans — the AM, creative team, the client. The “who is this property and how do we talk about it” document.", { bullet: true }),
      P("Lives on the HubSpot company record + a shareable Google Doc.", { bullet: true }),
      H2("B. The Community Brief (structured tagging surface)"),
      P("A structured form — sections and fields — capturing the property’s qualitative inputs as discrete, editable values. Sections: Identity, Voice & Positioning, Lifecycle, Inventory, Amenities, Geography, Competitors, Guardrails.", { bullet: true }),
      P("Consumer: machines first (the Fluency paid-media tag-sync reads these values to build ad targeting + copy), humans second (the reviewer who curates them).", { bullet: true }),
      P("Lives on HubSpot company fluency_* properties (pipeline) + fluency_*_override properties (human edits).", { bullet: true }),
      P("The relationship: the Property Brief is the story. The Community Brief is the structured facts that story (and our ad systems) draw from. Same identity, two representations — one prose, one data.", { italics: true, after: 160 }),

      H1("2. What kicks it off"),
      P("Everything starts with a ClickUp ticket."),
      mono("ClickUp ticket created (taskCreated webhook)"),
      mono("  -> PATH A (Commercial): parse ticket -> match/create HubSpot"),
      mono("     company -> deal + line items -> quote -> email RM"),
      mono("  -> PATH B (Brief): run LLM -> persist w/ token -> post the"),
      mono("     approval URL into ClickUp, tagging the submitter"),
      P("Trigger rules: always fires on ticket creation; on ticket update only fires if the re-process flag flips truthy (prevents an edited description from re-billing the LLM and re-creating deals).", { after: 80 }),
      P("Ticket must contain: Property Name, Submitter Email (or falls back to the AM/assignee), RM Email, channel selections, optionally Domain + Notes. Missing required fields comment back in ClickUp asking the submitter to fix them.", { after: 80 }),
      P("Second trigger: HubSpot quote-signed webhook posts “onboarding can begin” back into the originating ticket.", { after: 160 }),

      H1("3. How we generate it"),
      H2("Property Brief"),
      P("Scrape the property’s marketing site for grounding text.", { num: true }),
      P("Assemble prompt: name + domain + submitter notes + approved channels + any prior-revision feedback.", { num: true }),
      P("Claude (Sonnet) call, max 2,500 tokens. Every claim must be grounded in source material — “If a section can’t be supported, say ‘TBD — needs submitter input’ instead of guessing.” No invented stats, phones, or addresses.", { num: true }),
      H2("Community Brief"),
      P("Fields populate from two sources merged override-wins: pipeline values (auto-derived daily from Apt IQ + site scrape + voice-tier derivation, read-only) and override values (human edits on the form). Override always beats pipeline when the cron builds the live Fluency tags.", { after: 80 }),
      P("Each field shows one value + a source badge: Edited / Pipeline / Not set / Pending.", { after: 160 }),

      H1("4. How people update it"),
      H2("Property Brief — approve / request-edits loop"),
      P("Submitter gets a tokenized approval URL in ClickUp. Approve → writes to HubSpot company, generates the final Google Doc, updates the spend sheet, confirms in ClickUp. Needs edits (+ feedback) → re-runs the LLM with ALL prior feedback and posts a fresh URL. After 3 revisions → escalates to the ops queue (no infinite LLM loop).", { after: 80 }),
      H2("Community Brief — structured field editing"),
      P("The reviewer edits fields directly on the same tokenized page. Each save writes that field’s fluency_*_override property. Only fields with an override column are editable; Apt IQ facts (year built, floor plans) are read-only. Dropdowns validate against allowed values. Edits go live to Fluency on the next daily cron run.", { after: 160 }),

      H1("5. How we stay Fair Housing compliant"),
      P("Enforced in three places, with one known gap flagged for this review."),
      H2("Layer 1 — Community Brief LLM prompts (STRICT, in place)"),
      P("Both AI previews carry an explicit strict instruction: do not reference age, family status, race, ethnicity, religion, national origin, disability, schools, or school districts. Audience framing must stay psychographic (lifestyle, needs, amenity preferences, commute).", { italics: true, after: 80 }),
      H2("Layer 2 — Field design (in place)"),
      P("“Primary Motivations & Considerations” is intentionally psychographic, with a hint warning the reviewer off demographics. A dedicated “Things NOT to Say” guardrail field captures property-specific sensitive phrasing and feeds it to copy systems as hard exclusions.", { bullet: true }),
      H2("Layer 3 — Paid-media targeting guards (in place, separate system)"),
      P("Enforces minimum ad radius (15 mi Housing Special Ad Category) and blocks protected-class language in audience descriptors. Protects targeting, not brief copy — different surface, same goal.", { bullet: true }),
      new Paragraph({ spacing: { before: 120, after: 80 },
        shading: { fill: "FBEAEA", type: ShadingType.CLEAR },
        children: [new TextRun({ text: "KNOWN GAP — flag for review",
          bold: true, color: RED, size: 22 })] }),
      P("The Property Brief LLM prompt does NOT contain the strict Fair Housing instruction that the Community Brief prompts do. It asks for a “Target Audience” section and only requires claims be grounded in source material — it does not forbid age / family status / race / religion / national origin / disability / school references. Scraped apartment sites rarely contain protected-class language so risk is low, but it is not controlled. A site describing itself as “perfect for young professionals” or “a great family community” could surface that into the Target Audience section.", { after: 80 }),
      P("Recommended fix (pending this review’s sign-off): add the same strict Fair Housing block to the Property Brief system prompt and reframe “Target Audience” → “Audience & Positioning (psychographic).” ~5 lines of prompt change, no architecture impact.", { bold: true, after: 160 }),

      new Paragraph({ children: [new PageBreak()] }),
      H1("6. Feedback requested"),
      P("Please mark up the items below. Goal: confirm the brief carries everything both teams need, and that the Fair Housing posture is correct."),
      H2("For Branding / Creative"),
      P("Voice & Tone: one 4-tier Voice Tier (value/standard/lifestyle/luxury) — enough, or do you need tone attributes (playful vs. refined vs. understated) independent of price tier?", { num: true }),
      P("Differentiators: free-form prose + amenities. Right shape, or do you need a ranked “hero differentiators” field?", { num: true }),
      P("Marketed names vs. normalized names: the split between tag-matching names and branded names — does that work for creative, or should there be one canonical “creative-approved language” field?", { num: true }),
      P("Guardrails: “Must Include” + “Things NOT to Say.” Anything brand-side missing (logo usage, tagline lock, banned competitor comparisons)?", { num: true }),
      P("What’s NOT in the brief that creative needs? Photography direction? Brand color/asset references? Approved hero imagery?", { num: true }),
      H2("For Digital / Performance"),
      P("Channel Strategy section — enough for media planning, or should it pull the Loop forecast (projected leases per channel) once wired?", { num: true }),
      P("Competitors field — same-market rent peers, one per line. Enough, or do you need rate/concession context?", { num: true }),
      H2("For Both — Fair Housing"),
      P("Confirm the recommended fix in section 5 (add the strict FHA block to the Property Brief prompt + reframe Target Audience). Approve as-is, or specify different enforced language.", { num: true }),
      P("Is the protected-topics list complete for our markets, or are there state/local categories to add (e.g., source of income, protected in some jurisdictions)?", { num: true }),
      H2("Open question for Kyle + peer"),
      P("Should the Property Brief and Community Brief converge into one surface over time, or stay as prose + structured-data counterparts? Current design keeps them separate on purpose — confirm that’s still right as both teams adopt it.", { num: true }),
    ],
  }],
});

const outDir = path.join(os.homedir(), "Downloads");
const outPath = path.join(outDir, "Client_Brief_System_Review.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(outPath, buf);
  console.log("WROTE " + outPath);
});
