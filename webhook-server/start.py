"""WSGI startup — uses waitress (threaded, no fork, Python 3.13 safe)."""
import os
import sys

# Ensure this directory is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from waitress import serve
from server import app

port = int(os.environ.get("PORT", 8080))
print(f"=== RPM Portal starting on 0.0.0.0:{port} ===", flush=True)
serve(app, host="0.0.0.0", port=port, threads=4)
