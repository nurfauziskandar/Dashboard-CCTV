from app.routes.dashboard import bp as dashboard_bp
from app.routes.cameras import bp as cameras_bp
from app.routes.servers import bp as servers_bp
from app.routes.auth import bp as auth_bp
from app.routes.summary import bp as summary_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(servers_bp)
    app.register_blueprint(summary_bp)
