"""Flask blueprints — one file per feature cluster.

Add new blueprints here as they're extracted from server.py. server.py
calls `register_all(app)` once at startup.
"""

from .clickup import clickup_bp
from .loop import loop_bp
from .onboarding import onboarding_bp
from .paid import paid_bp
from .portal import portal_bp
from .portal_ui import portal_ui_bp
from .property_brief import property_brief_bp
from .redlight import redlight_lite_bp
from .self_checkout import self_checkout_bp
from .seo import seo_bp
from .webhooks import register_webhook_blueprints


def register_all(app):
    """Register every blueprint in this package on the given Flask app."""
    app.register_blueprint(paid_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(portal_ui_bp)
    app.register_blueprint(redlight_lite_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(property_brief_bp)
    app.register_blueprint(loop_bp)
    app.register_blueprint(clickup_bp)
    # Loop 1 self-checkout — every endpoint 404s until SELF_CHECKOUT_ENABLED=true,
    # so registering here is inert until you flip the flag.
    app.register_blueprint(self_checkout_bp)
    register_webhook_blueprints(app)
