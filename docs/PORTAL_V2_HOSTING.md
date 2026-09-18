# The client workspace at digital.rpmliving.com/client-portal/v2

The workspace is one app, served from Render, reachable at two URLs:

| URL | Host | What serves it |
|---|---|---|
| `https://rpm-portal-server.onrender.com/workspace` | Render | `routes/portal_ui.py → /workspace` |
| `https://digital.rpmliving.com/client-portal/v2` | HubSpot CMS | `hubspot-cms/templates/client-portal-v2.html`, which loads `/workspace/embed.js` from Render |

The API stays on Render at `/api/workspace/*` in both cases. On Render the
page and the API are the same origin. On HubSpot they are not, and that is the
only difference between the two.

## How the HubSpot page works

The template is a **loader**, about 4 KB. It renders `<div id="rpm-workspace">`
and loads `https://rpm-portal-server.onrender.com/workspace/embed.js?v=<app_version>`.

`/workspace/embed.js` is **generated from `portal_pages/workspace.html` on every
request** (`routes/portal_ui.py: build_workspace_embed`). It injects that page's
stylesheet and markup, then runs that page's app script. So:

- there is exactly **one copy of the app**, and both URLs run the same bytes;
- shipping an app change is a Render deploy — no template upload, no CDN wait;
- the template changes about once a year.

The app is **first-party** on `digital.rpmliving.com`: no iframe, so Clerk's
session is not third-party storage (Safari blocks that, Chrome partitions it),
and printing, deep links and the Back button all behave normally. Only the API
calls cross an origin, as CORS.

### Same-origin behaviour is unchanged by design

`workspace.html` carries `window.__PORTAL_API_BASE__ = ''`. Empty means
same-origin: relative URLs, `credentials: 'same-origin'`, exactly what shipped.
`/workspace` injects nothing unless `PORTAL_API_BASE` is set, so the bytes it
serves are the bytes in the file — `tests/test_workspace_page.py` asserts that
identity, and `tests/test_workspace_v2_hosting.py` asserts the request shaping.

Cross-origin (the base is an absolute `https://` origin) the app switches to
absolute URLs, the Clerk `Authorization: Bearer` token, and
`credentials: 'omit'`. **No cookie ever crosses an origin**, so nothing here
depends on third-party cookies. A value that is not a bare absolute origin is
ignored rather than trusted, because it is pasted into the URL a Bearer token
is sent to.

### The monthly report

`/workspace/report` is a page on the API host, so its link moves with the API
base. Cross-origin, the workspace also appends `?app=<this page's URL>` so the
report's sidebar and back link come back to the HubSpot page instead of to
Render. The report page honours `app` only for its own origin and the origins
in its `APP_ORIGINS` list (`https://digital.rpmliving.com`) — an unchecked
value would make every link on that page an open redirect.

If the report page is ever hosted off Render too, it already reads
`window.__PORTAL_API_BASE__`; whoever owns `routes/workspace_report.py` needs
to inject it there the same way `portal_ui.py` does.

## Environment variables

### What Kyle sets for /v2 today (Render)

| Variable | Value today | Why |
|---|---|---|
| `WORKSPACE_ENABLED` | `true` | Gates `/workspace`, `/workspace/embed.js` and every `/api/workspace/*` route. Without it all of them 404. |
| `CLERK_PUBLISHABLE_KEY` | the **dev** `pk_test_…` | Injected into the page and into the embed at serve time. The Clerk Frontend API domain and the JWKS URL are both derived from this key, which is what makes the swap one variable. |
| `CLERK_SECRET_KEY` | the **dev** `sk_test_…` | Server-side: looks up the signed-in user's email after the token verifies. Must be from the same instance as the publishable key. |
| `WORKSPACE_REQUIRE_PROOF` | `true` (the default — leave unset) | Every `/api/workspace/*` answer requires a proven identity, not an asserted `X-Portal-Email`. |

**Not needed today:**

- `PORTAL_API_BASE` — leave unset. `/workspace` is same-origin, and
  `/workspace/embed.js` defaults the base to **its own public origin**, which is
  by definition the API host. Set it only when the API moves to its own
  hostname (e.g. `https://api.rpmliving.com`); it must be a bare origin, no
  path and no trailing slash.
- `CLERK_AUTHORIZED_PARTIES` — unset means "accept a token from any origin",
  which is the current behaviour. **If you set it, it must include
  `https://digital.rpmliving.com`**, or every token minted on the HubSpot page
  is rejected and clients get a sign-in loop with a 401 behind it.
- `CLERK_JWKS_URL` — leave unset. It overrides the JWKS URL derived from the
  publishable key, so a value here would pin verification to the old Clerk
  instance after the swap and every token would fail. This is the one variable
  that can silently undo the swap.

`digital.rpmliving.com` is already in `ALLOWED_ORIGINS` (`_route_utils.py`), so
CORS needs no change. Preflights answer with `Authorization` allowed and
`Access-Control-Max-Age: 7200`, so a burst of calls is not preflighted twice.

### HubSpot side (one-time)

1. Upload `hubspot-cms/templates/client-portal-v2.html` and create a page from
   it with the slug `client-portal/v2`.
2. Record its IDs: `HUBSPOT_V2_TEMPLATE_ID` and `HUBSPOT_V2_PAGE_ID` in `.env`,
   then paste them into `TARGETS` in `scripts/deploy_template.py` so nobody has
   to guess them again (DEPLOY.md Trap 1).
3. Deploy changes with `python3 scripts/deploy_template.py --target workspace-v2`.
   Bump `app_version` in the template first: Cloudflare holds the page HTML for
   ~10 hours (Trap 3), and the version is both the cache-busting handle and the
   way to tell which render is live (`window.__RPM_WORKSPACE_EMBED__.version`
   in the console).

## The Clerk swap, when production DNS on rpmliving.com lands

**Change one variable for the page:**

```
CLERK_PUBLISHABLE_KEY = pk_live_…        # the production instance
```

Everything the browser needs follows from it: the page reads it at serve time,
and the Clerk script URL, the Frontend API domain and the JWKS URL are all
derived from it. Nothing about the instance is hardcoded in the page, the
template or the loader.

**Then keep the API in step:**

```
CLERK_SECRET_KEY = sk_live_…             # same instance as the key above
CLERK_AUTHORIZED_PARTIES                 # only if set: include every origin
                                         # that mints tokens, i.e.
                                         # https://digital.rpmliving.com
CLERK_JWKS_URL                           # must stay UNSET
```

No code, template or page change. No redeploy of the HubSpot template — the key
is never in it.

### A bad key fails loudly, not blankly

This was a requirement, and it is tested:

- **Missing/not a `pk_`** — cross-origin the app can never authenticate
  (cookies are omitted), so instead of an empty shell it renders
  "Sign in to see your workspace" with "Sign-in is not configured for this page
  yet…". The server logs `workspace embed served without CLERK_PUBLISHABLE_KEY`.
- **Present but wrong** — Clerk's script fails to load and the app shows
  "We could not reach the sign-in service."
- **Valid key, wrong instance for the API** — calls 401 and the app shows the
  sign-in state rather than a half-rendered page.
- **Loader unreachable** — the template's `onerror` and its 15-second watchdog
  replace "Loading your workspace…" with a plain-language failure.

## Files

| File | Role |
|---|---|
| `webhook-server/portal_pages/workspace.html` | the app; the only copy |
| `webhook-server/portal_pages/workspace_report.html` | the monthly report page |
| `webhook-server/routes/portal_ui.py` | serves `/workspace`, generates `/workspace/embed.js` |
| `hubspot-cms/templates/client-portal-v2.html` | the HubSpot loader page |
| `scripts/deploy_template.py` | `--target workspace-v2` |
| `tests/test_workspace_v2_hosting.py` | page rendering, embed, CORS preflight |
| `tests/js/workspace_api_base_harness.js` | the app's real request shaping and report links, in node |
