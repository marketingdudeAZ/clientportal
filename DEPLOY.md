# Deploying the client portal template to HubSpot

**TL;DR — always use:**
```
python3 scripts/deploy_template.py
```

That's it. The script handles all the traps below.

---

## Why you can't just `hs cms upload`

Two historical traps that cost us ~6 hours of debugging:

### Trap 1: Multiple template files with similar names

HubSpot's Design Manager has had 4 files named variations of `client-portal.html`:
- `client-portal.html` (root) — **orphan, deleted**
- `custom/client-portal/Client Portal.html` — **orphan, deleted**
- `custom/pages/rpm-portal/client-portal.html` — **orphan, deleted**
- `templates/client-portal.html` — **the real one, KEEP**

`hs cms upload client-portal.html` defaults to the root path and silently reports success even when the renderer reads from a different file.

Only `templates/client-portal.html` (template ID `210982557303`) is bound to the live page (page ID `209266222927`). The deploy script hard-codes these IDs so no path guessing happens.

### Trap 2: Two separate HubSpot template APIs

HubSpot has TWO APIs for template source code:
| API | Path | What it does |
|---|---|---|
| v3 Source Code | `cms/v3/source-code/...` | Newer, what `hs cms` uses |
| v2 Content | `content/api/v2/templates/{id}` | **What the page renderer actually reads** |

These are **not synced.** Uploads to v3 don't propagate to v2. You must push to the v2 endpoint for the live page to see your changes.

### Trap 3: Cloudflare prerender cache

HubSpot's CDN prerenders pages and holds the HTML at the edge for up to **10 hours** (`s-maxage=36000`). Template updates don't auto-invalidate this cache.

Reliable busts:
- **Change the URL slug** → new Cloudflare cache key (instant fix)
- Wait ~10 hours for TTL
- HubSpot UI "Update" button sometimes works, sometimes doesn't

Our current live slug is `portal-dashboard` (we changed it from `client-portal` on 2026-04-22 to bust a stuck cache).

### Trap 4: HubL branch conflict — THE biggest landmine

The template has two HubL branches:

```hubl
{% if uuid_param %}
  <!-- Property detail view — lines 11 to ~7514 -->
{% else %}
  <!-- Portfolio dashboard view — lines ~7515 to end -->
{% endif %}
```

**If you add JavaScript to the END of the file, it lands inside the `{% else %}` branch** and will never execute on property detail pages (the main client portal URL).

All Phase 1/2/3 code (`loadSeoInsights`, `loadContentPlanner`, `loadKeywordResearch`, entitlement checks, etc.) **must** go BEFORE the `{% else %}` marker.

`scripts/deploy_template.py` validates this before upload — if your JS is in the wrong branch, the script refuses to deploy and tells you to move it.

---

## Canonical constants (don't guess)

| Thing | Value |
|---|---|
| Template path (for reference) | `templates/client-portal.html` |
| Template ID (v2 API) | `210982557303` |
| Page ID | `209266222927` |
| Live URL | `https://digital.rpmliving.com/portal-dashboard` |
| HubSpot Page edit URL | `https://app.hubspot.com/website/19843861/pages/209266222927/edit` |
| HubSpot portal ID | `19843861` |

---

## The deploy workflow

1. Edit `hubspot-cms/templates/client-portal.html` locally.
2. **Make sure JS changes are inside the `{% if uuid_param %}` branch** (before `{% else %}`).
3. Run `python3 scripts/deploy_template.py`.
4. Script does:
   - Validates HubL branch structure (fails fast if JS is in wrong branch)
   - Uploads via v2 Content API (what the renderer reads from)
   - Verifies storage
   - Force-republishes the page
   - Polls the live URL up to 180s
5. If the live URL still looks stale after the script runs, the script prints manual instructions to change the URL slug in HubSpot UI.

## Never do this

- ❌ **Never paste** large template content into HubSpot's web code editor. `pbcopy` + paste corrupts UTF-8 (spent 2 hours un-corrupting mojibake `‚Ä¶`).
- ❌ **Never append** new JavaScript to the end of `client-portal.html` without checking it's above `{% else %}`.
- ❌ **Never use** `hs cms upload` for this template — it hits the wrong path.
- ❌ **Never create** a new template file named `client-portal.html` at any other path. We just cleaned up 3 orphans.

## If something's wrong on the live page

1. Run `python3 scripts/deploy_template.py` again — it's idempotent.
2. If it exits with code 2 (stale after 3 min), follow printed instructions to change slug.
3. Check browser console — if nav items are in DOM but `display:none`, the entitlement check didn't fire. Look at:
   - Network tab: did `/api/property` + `/api/seo/entitlement` both 200?
   - Console: any JS errors before our IIFEs?
   - `window.__PORTAL_PROP_PACKAGES` — should have an `seo_organic` entry for properties with SEO tier
