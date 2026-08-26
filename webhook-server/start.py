"""WSGI startup with full diagnostic output so Render Deploy Logs show any crash."""
import os
import sys
import traceback

print("=== STEP 1: Python started ===", flush=True)
print(f"  Python: {sys.version}", flush=True)
print(f"  PORT env: {os.environ.get('PORT', 'NOT SET')}", flush=True)
print(f"  __file__: {__file__}", flush=True)

# Ensure this directory is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    port = int(os.environ.get("PORT", 8080))
    print(f"=== STEP 2: port={port} ===", flush=True)

    print("=== STEP 3: importing waitress ===", flush=True)
    from waitress import serve
    print("=== STEP 3 OK ===", flush=True)

    print("=== STEP 4: importing Flask app ===", flush=True)
    from server import app
    print(f"=== STEP 4 OK — {len(list(app.url_map.iter_rules()))} routes ===", flush=True)

    # I/O-bound service: every thread sits blocked in `requests` against
    # HubSpot / ClickUp / BigQuery at ~0 CPU, so the ceiling is MEMORY, not
    # cores. 4 was waitress's default, never a decision — and one
    # /api/portal-tickets load could hold 1 of those 4 for the length of a
    # serial ClickUp fan-out, so a handful of colleagues on the tickets tab
    # made the whole portal unreachable.
    #
    # 16 is safe on a 512MB instance (~8MB of stack per worker thread plus a
    # request context); 24 is reasonable on 2GB. CHECK THE ACTUAL INSTANCE SIZE
    # before raising further — the repo disagrees with itself about the host
    # (requirements.txt says Railway, DEPLOY.md says Render) and there is no
    # deploy manifest to read it from. Memory exhaustion is a crash, not a
    # slowdown.
    #
    # This multiplies against portal_tickets._FETCH_WORKERS (4), so the
    # process-wide worst case is 16 × 4 = 64 concurrent ClickUp sockets.
    threads = int(os.environ.get("WEB_THREADS", "16"))
    print(f"=== STEP 5: starting waitress on 0.0.0.0:{port} (threads={threads}) ===",
          flush=True)
    serve(app, host="0.0.0.0", port=port,
          threads=threads,
          connection_limit=200,   # queue a burst rather than refuse it
          channel_timeout=60)     # reap idle/abandoned connections
    print("=== waitress exited normally ===", flush=True)

except SystemExit as e:
    print(f"=== SYSTEM EXIT: code={e.code} ===", flush=True)
    sys.exit(e.code)
except Exception as e:
    print(f"=== FATAL CRASH: {type(e).__name__}: {e} ===", flush=True)
    traceback.print_exc()
    sys.exit(1)
