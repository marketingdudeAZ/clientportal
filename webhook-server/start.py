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

    print(f"=== STEP 5: starting waitress on 0.0.0.0:{port} ===", flush=True)
    serve(app, host="0.0.0.0", port=port, threads=4)
    print("=== waitress exited normally ===", flush=True)

except SystemExit as e:
    print(f"=== SYSTEM EXIT: code={e.code} ===", flush=True)
    sys.exit(e.code)
except Exception as e:
    print(f"=== FATAL CRASH: {type(e).__name__}: {e} ===", flush=True)
    traceback.print_exc()
    sys.exit(1)
