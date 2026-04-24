# Runbook

Day-to-day operations for the RPM Client Portal. Start here when onboarding or when something is on fire.

## Run locally

Prereqs: Python 3.11+, a populated `.env` at the repo root (copy `.env.example`), and access to the HubSpot private app token.

```bash
# From the repo root
pip install -r requirements.txt    # pulls in webhook-server/requirements.txt via -r
cp .env.example .env               # then fill in values you have

# Start the Flask server
cd webhook-server
python start.py                    # Waitress on PORT or 8080
# Alternative for Flask dev reloader:
# FLASK_ENV=development FLASK_APP=server.py flask run --port 8080
```

Hit `http://localhost:8080/health` to confirm the server is up and the route map loaded cleanly.

**Minimum env vars for the server to boot**: `HUBSPOT_API_KEY`, `WEBHOOK_SECRET`. Without those, startup aborts; every other integration degrades gracefully when its keys are missing.

## Deploy to production

Deploys are GitOps-driven. Pushing to `main` triggers Railway.

```bash
# Normal release: land on main and let Railway deploy automatically
git push origin main
```

Railway configuration (already in the repo, do not re-create):
- `webhook-server/railway.toml` — start command, health check, restart policy
- `webhook-server/Procfile` — `web: python start.py` fallback
- `webhook-server/requirements.txt` — authoritative dependency list

Watch the deploy:
1. Open the Railway dashboard for the project.
2. Tail the Deploy Logs. `start.py` prints numbered `STEP` markers so you can see exactly where a crash happens (Python import, Flask app load, Waitress bind).
3. The health check hits `/health` with a 300s grace window. If it fails, Railway rolls back.

**Rolling the portal template** (HubSpot CMS side) is separate from Railway:

```bash
python scripts/deploy_template.py       # pushes hubspot-cms/templates/ to Design Manager
python scripts/deploy_to_hubspot.py     # bigger bundle (css/js/images/partials)
```

See `DEPLOY.md` for the HubL branch trap: new JS appended to `client-portal.html` ends up in the portfolio `{% else %}` branch and never runs on the property detail view. Add it inside the property branch.

## Add a new endpoint

1. **Pick the module.** Follow the existing pattern in `webhook-server/server.py` (or the blueprint file under `webhook-server/routes/` once the split lands). Keep business logic in a dedicated module (e.g. `keyword_research.py`); the route handler should be thin.
2. **Choose an auth guard.**
   - Portal-user endpoints: read `X-Portal-Email` from the request header. Every existing route does this — copy the pattern.
   - Server-to-server / cron: decorate with `@require_internal_key` from `auth.py`.
   - Inbound webhooks: HMAC-validate the body. Configurator uses `hmac_validator.validate_signature`; video providers validate in their `normalize_webhook` methods.
3. **Return JSON, always.** `jsonify(...)` with the correct status code. Errors return `{"error": "...", "detail": "..."}` and a 4xx/5xx.
4. **Write a test.** Put it under `tests/`. The auth-coverage test (`tests/test_auth_coverage.py`) iterates `app.url_map` and enforces that every POST/PATCH/DELETE route has a guard — a new unprotected route fails the test.
5. **Update docs.** Add the route to `docs/ARCHITECTURE.md` if it introduces a new user-visible journey or a new external integration.

## Add a new HubDB table

1. Add the table definition to `scripts/create_hubdb_tables_v2.py` (schema: label + columns + types + options).
2. Run the script against the target portal; capture the returned table ID.
3. Add an env var for the table ID: `HUBDB_<THING>_TABLE_ID=`. Put it in `.env.example` with a one-line comment.
4. Export it from `webhook-server/config.py` (and `config.py` at root if it's read from scripts).
5. Use the shared helpers: `read_rows`, `insert_row`, `update_row`, `delete_row`, `publish` from `hubdb_helpers.py`. Writes raise `HubDBError` on failure — catch it only if the batch should continue past one row.
6. Watch out for DATETIME columns: HubDB wants **milliseconds since epoch as int**, not an ISO string. There's precedent in `ai_mentions.py` and `onboarding_keywords.py`.

## Add a new integration

1. Drop a new env var group into `.env.example` with a header comment.
2. Read the env var in `webhook-server/config.py` and export it.
3. Create a thin client module named after the integration (`foo_client.py`). Keep HTTP + auth + parsing in there; expose function-level helpers.
4. Add a `test_<foo>_client.py` under `tests/` that mocks `requests` and asserts the wire format. Follow `test_dataforseo_client.py`.
5. If the integration has a webhook: add an `@app.route` and validate its signature. Match the fail-closed pattern used by `heygen_provider.py` and `creatify_provider.py`.

## Troubleshooting

| Symptom                                              | First thing to check                                          |
|------------------------------------------------------|---------------------------------------------------------------|
| `/health` 404 or 502 in Railway                      | Deploy logs — look for a `STEP` marker that didn't complete   |
| Portal page loads but API calls 401                  | `X-Portal-Email` header not reaching Flask — inspect Network tab |
| SEO refresh finishes but dashboard empty             | HubDB DATETIME format — check logs for `HubDB insert failed` with response body |
| Video webhook returns 401 in production              | `HEYGEN_WEBHOOK_SECRET` or `CREATIFY_WEBHOOK_SECRET` not set — add to Railway |
| Config change deployed but not picked up             | Railway requires an explicit Redeploy after env var edits     |
| Portal template updated but property detail unchanged| HubL branch trap — see `DEPLOY.md`                            |
| `ModuleNotFoundError` at startup                     | Dep missing from `webhook-server/requirements.txt` (root file is a re-export, edit the subdir version) |

## Known foundations work in flight

The foundation stabilization plan (see git log for "Foundation pass" commits) is in progress. Phases:

1. ✅ Security + data integrity (internal-key decorator, webhook HMAC, HubDB raise-on-error, `.env.example` rewrite, requirements.txt consolidation)
2. 🟡 Structure + docs (this runbook, `ARCHITECTURE.md`, `server.py` blueprint split)
3. ⏳ Tests (auth-coverage guard, journey tests, HubDB contract tests, rewrite `test_deal_creation.py`)

Portal-side signed-request auth (replacing `X-Portal-Email` trust) is deliberately deferred until testers are off the shared portal — see the plan for the rollout sequencing.
