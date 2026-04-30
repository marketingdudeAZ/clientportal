"""Flask blueprints — one file per feature cluster.

Add new blueprints here as they're extracted from server.py. server.py
calls `register_all(app)` once at startup.
"""

from .onboarding import onboarding_bp
from .paid import paid_bp
from .property_brief import property_brief_bp
from .seo import seo_bp


def register_all(app):
    """Register every blueprint in this package on the given Flask app."""
    app.register_blueprint(paid_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(property_brief_bp)
