"""Flask blueprints — one file per feature cluster.

Add new blueprints here as they're extracted from server.py. server.py
calls `register_all(app)` once at startup.
"""

from .paid import paid_bp


def register_all(app):
    """Register every blueprint in this package on the given Flask app."""
    app.register_blueprint(paid_bp)
