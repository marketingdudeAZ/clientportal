/* Onboarding intake — wires the form to the Flask API.
 *
 * Endpoints used (all under WEBHOOK_SERVER_URL — set in client-portal.html):
 *   GET  /api/onboarding/intake/draft         AI strawman + this/that picks
 *   POST /api/onboarding/managers/derive-name first.last@... → "First Last"
 *   POST /api/onboarding/colors/extract       extract swatches from logo
 *   POST /api/onboarding/colors/save          persist approved primary/secondary
 *   POST /api/onboarding/assets/upload        upload + variant generation
 *   POST /api/onboarding/intake               submit the whole form
 *
 * Anti-slop UX patterns:
 *   - Multi-select dropdowns disable free typing
 *   - "this or that" cards force a binary pick
 *   - Concession/occupancy patterns reject prose at the input level
 *   - Submit triggers server-side AI-slop classifier on free-text fields
 */

(function () {
  const apiBase = window.RPM_PORTAL_API_BASE || "";
  const portalEmail = window.RPM_PORTAL_EMAIL || "";
  const form = document.getElementById("onboarding-form");
  if (!form) return;

  const companyId = form.dataset.companyId;
  const propertyUuid = form.dataset.uuid;

  function api(path, opts) {
    return fetch(apiBase + path, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        "X-Portal-Email": portalEmail,
        "X-Company-Id": companyId,
        ...(opts && opts.headers),
      },
    }).then((r) => r.json().then((data) => ({ ok: r.ok, status: r.status, data })));
  }

  function apiUpload(path, formData) {
    return fetch(apiBase + path, {
      method: "POST",
      headers: {
        "X-Portal-Email": portalEmail,
        "X-Company-Id": companyId,
      },
      body: formData,
    }).then((r) => r.json().then((data) => ({ ok: r.ok, status: r.status, data })));
  }

  // ── 1. Hydrate AI strawman + this-or-that ─────────────────────────────
  api("/api/onboarding/intake/draft").then(({ ok, data }) => {
    if (!ok) return;
    const tot = document.getElementById("ob-this-or-that");
    if (tot && Array.isArray(data.this_or_that)) {
      tot.innerHTML = data.this_or_that
        .map(
          (q, i) => `
        <div class="this-or-that-card">
          <div class="t-or-t-label">${q.field.replace(/_/g, " ")}</div>
          <div class="t-or-t-options">
            ${q.options
              .map(
                (opt, j) => `
              <label class="t-or-t-option">
                <input type="radio" name="${q.field}" value="${escapeAttr(opt)}" ${j === 0 ? "checked" : ""} />
                <span>${escapeHtml(opt)}</span>
              </label>`,
              )
              .join("")}
          </div>
        </div>`,
        )
        .join("");
    }

    // Hydrate competitor and neighborhood selects from AI draft
    if (data.draft) {
      const competitors = (data.draft.primary_competitors || "").split(/[;,]/).map(s => s.trim()).filter(Boolean);
      const neighborhoods = (data.draft.neighborhoods_to_target || "").split(/[;,]/).map(s => s.trim()).filter(Boolean);
      populateSelect("ob-competitors", competitors);
      populateSelect("ob-neighborhoods", neighborhoods);
    }
  });

  function populateSelect(id, options) {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = options
      .map((o) => `<option value="${escapeAttr(o)}">${escapeHtml(o)}</option>`)
      .join("");
  }

  // ── 2. Live-derive manager names from email ────────────────────────────
  ["ob-cm-email", "ob-rm-email"].forEach((inputId) => {
    const input = document.getElementById(inputId);
    const previewId = inputId.replace("-email", "-name-preview");
    const preview = document.getElementById(previewId);
    if (!input || !preview) return;
    input.addEventListener("blur", () => {
      const email = input.value.trim();
      if (!email) {
        preview.textContent = "";
        return;
      }
      api("/api/onboarding/managers/derive-name", {
        method: "POST",
        body: JSON.stringify({ email }),
      }).then(({ data }) => {
        if (data && data.name) {
          preview.textContent = "→ " + data.name;
          preview.style.color = "#1a6b1a";
        } else {
          preview.textContent = "Email format not recognized — must be first.last@rpmliving.com";
          preview.style.color = "#b00020";
        }
      });
    });
  });

  // ── 3. Logo upload → extract colors → render swatches → pick primary/sec ──
  const logoInput = document.getElementById("ob-logo");
  const swatchesEl = document.getElementById("ob-color-swatches");
  let pickedColors = { primary: null, secondary: null };

  if (logoInput) {
    logoInput.addEventListener("change", async () => {
      if (!logoInput.files || !logoInput.files[0]) return;
      swatchesEl.innerHTML = "<small>Extracting colors…</small>";
      const fd = new FormData();
      fd.append("file", logoInput.files[0]);
      const { ok, data } = await apiUpload("/api/onboarding/colors/extract", fd);
      if (!ok) {
        swatchesEl.innerHTML = `<small style="color:#b00020">${escapeHtml(data.error || "Color extraction failed")}</small>`;
        return;
      }
      renderSwatches(data.colors || []);
    });
  }

  function renderSwatches(colors) {
    if (!colors.length) {
      swatchesEl.innerHTML = "<small>No colors extracted from logo.</small>";
      return;
    }
    swatchesEl.innerHTML = `
      <div class="swatch-help">Pick your <strong>primary</strong> and <strong>secondary</strong> brand colors:</div>
      <div class="swatch-grid">
        ${colors
          .map(
            (c) => `
          <button type="button" class="swatch" data-hex="${c}" style="background:${c}" title="${c}">
            <span class="swatch-hex">${c}</span>
          </button>`,
          )
          .join("")}
      </div>
      <div class="swatch-picks">
        <span>Primary: <em id="swatch-primary">—</em></span>
        <span>Secondary: <em id="swatch-secondary">—</em></span>
      </div>
    `;
    swatchesEl.querySelectorAll(".swatch").forEach((btn) => {
      btn.addEventListener("click", () => {
        const hex = btn.dataset.hex;
        if (!pickedColors.primary) {
          pickedColors.primary = hex;
          document.getElementById("swatch-primary").textContent = hex;
          btn.classList.add("swatch-picked-primary");
        } else if (!pickedColors.secondary && hex !== pickedColors.primary) {
          pickedColors.secondary = hex;
          document.getElementById("swatch-secondary").textContent = hex;
          btn.classList.add("swatch-picked-secondary");
        } else {
          // Reset on third click
          pickedColors = { primary: null, secondary: null };
          swatchesEl.querySelectorAll(".swatch").forEach((b) => {
            b.classList.remove("swatch-picked-primary", "swatch-picked-secondary");
          });
          document.getElementById("swatch-primary").textContent = "—";
          document.getElementById("swatch-secondary").textContent = "—";
        }
      });
    });
  }

  // ── 4. Submit ──────────────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = document.getElementById("ob-status");
    const submitBtn = document.getElementById("ob-submit");
    submitBtn.disabled = true;
    status.textContent = "Submitting…";

    const formData = new FormData(form);
    const payload = {};
    for (const [k, v] of formData.entries()) {
      if (payload[k] !== undefined) {
        if (!Array.isArray(payload[k])) payload[k] = [payload[k]];
        payload[k].push(v);
      } else {
        payload[k] = v;
      }
    }
    // Multi-selects collected by name above — also harvest from <select multiple>
    document.querySelectorAll('select[multiple]').forEach((sel) => {
      payload[sel.name] = Array.from(sel.selectedOptions).map((o) => o.value);
    });
    payload.property_uuid = propertyUuid;

    // Upload assets first so we can attach references to the intake row
    const logoFile = logoInput && logoInput.files && logoInput.files[0];
    const heroInput = document.getElementById("ob-hero");
    const heroFile = heroInput && heroInput.files && heroInput.files[0];
    if (logoFile) {
      const fd = new FormData();
      fd.append("file", logoFile);
      fd.append("asset_kind", "logo");
      fd.append("property_uuid", propertyUuid);
      await apiUpload("/api/onboarding/assets/upload", fd);
    }
    if (heroFile) {
      const fd = new FormData();
      fd.append("file", heroFile);
      fd.append("asset_kind", "hero");
      fd.append("property_uuid", propertyUuid);
      await apiUpload("/api/onboarding/assets/upload", fd);
    }
    if (pickedColors.primary && pickedColors.secondary) {
      await api("/api/onboarding/colors/save", {
        method: "POST",
        body: JSON.stringify({
          property_uuid: propertyUuid,
          primary: pickedColors.primary,
          secondary: pickedColors.secondary,
        }),
      });
    }

    const { ok, data } = await api("/api/onboarding/intake", {
      method: "POST",
      body: JSON.stringify({ company_id: companyId, property_uuid: propertyUuid, payload }),
    });
    if (!ok) {
      status.textContent = "Error: " + (data.error || "submit failed");
      submitBtn.disabled = false;
      return;
    }
    if (data.gap_questions && data.gap_questions.length) {
      status.innerHTML = `
        Submitted with ${data.gap_questions.length} item${data.gap_questions.length === 1 ? "" : "s"} flagged for follow-up.
        Your CSM will email the Community Manager for verification.
      `;
    } else {
      status.textContent = "Intake submitted — clean. CSM will be in touch shortly.";
    }
  });

  // ── helpers ────────────────────────────────────────────────────────────
  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }
  function escapeAttr(s) {
    return escapeHtml(s);
  }
})();
