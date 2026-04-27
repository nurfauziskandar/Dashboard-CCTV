import os
import logging
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from flask import Flask, session, redirect, url_for, request
from config import config_map


def _setup_logging(app):
    logs_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    fmt = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Rotating file handler — 5 MB per file, keep 5 backups
    file_handler = RotatingFileHandler(
        os.path.join(logs_dir, 'app.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.DEBUG if app.config.get('DEBUG') else logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    else:
        # App already has handlers (e.g. reloader), just add file handler
        root.addHandler(file_handler)

    app.logger.info(
        'Dashboard-CCTV starting — env=%s DEMO_MODE=%s',
        app.config.get('_ENV_NAME', 'unknown'),
        app.config.get('DEMO_MODE'),
    )


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_map.get(config_name, config_map['default']))
    app.config['_ENV_NAME'] = config_name

    # Session security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

    os.makedirs(app.instance_path, exist_ok=True)
    _setup_logging(app)

    # Global login guard
    _PUBLIC_ENDPOINTS = {'auth.login', 'auth.logout', 'static'}

    @app.before_request
    def _require_login():
        if request.endpoint in _PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if not session.get('user'):
            return redirect(url_for('auth.login', next=request.path))
        return None

    # Init extensions
    from app.extensions import db, scheduler
    db.init_app(app)

    # Import models so SQLAlchemy knows about them
    from app.models import Camera, Server, HDD, PSU, StatusSnapshot  # noqa: F401

    # Register blueprints
    from app.routes import register_blueprints
    register_blueprints(app)

    with app.app_context():
        db.create_all()

        # Init services
        from app.services.camera_service import CameraService
        from app.services.server_service import ServerService

        camera_service = CameraService(app)
        server_service = ServerService(app)

        if app.config['DEMO_MODE']:
            from app.services.demo.fake_stream import FakeStreamService
            stream_service = FakeStreamService()
        else:
            from app.services.stream_service import StreamService
            stream_service = StreamService()

        from app.services.storage_client import StorageClient
        storage_client = StorageClient(
            base_url=app.config.get('STORAGE_URL', ''),
            api_token=app.config.get('STORAGE_API_TOKEN', ''),
            timeout=app.config.get('STORAGE_TIMEOUT', 5),
        )

        from app.services.snapshot_service import SnapshotService
        snapshot_service = SnapshotService(app)

        app.config['camera_service'] = camera_service
        app.config['server_service'] = server_service
        app.config['stream_service'] = stream_service
        app.config['storage_client'] = storage_client
        app.config['snapshot_service'] = snapshot_service
        camera_service.storage_client = storage_client

        # Seed demo data if in demo mode and DB is empty
        if app.config['DEMO_MODE']:
            _seed_demo_data(db, camera_service, server_service)

    # Background polling
    if not app.config.get('TESTING'):
        app.config['SCHEDULER_API_ENABLED'] = False
        scheduler.init_app(app)

        @scheduler.task(
            'interval',
            id='poll_cameras',
            seconds=app.config['CAMERA_POLL_INTERVAL'],
        )
        def poll_cameras():
            camera_service.poll_all()

        @scheduler.task(
            'interval',
            id='poll_servers',
            seconds=app.config['SERVER_POLL_INTERVAL'],
        )
        def poll_servers():
            server_service.poll_all()

        @scheduler.task('cron', id='daily_snapshot', hour=0, minute=0)
        def daily_snapshot():
            snapshot_service.capture()

        scheduler.start()

        # Capture an initial snapshot for today on startup so summary shows
        # data even before the first midnight tick.
        try:
            snapshot_service.capture()
        except Exception as exc:
            app.logger.warning('Initial snapshot capture failed: %s', exc)

    return app


def _seed_demo_data(db, camera_service, server_service):
    from app.models.camera import Camera
    from app.models.server import Server

    if Camera.query.count() == 0:
        from app.services.demo.fake_cameras import DEMO_CAMERAS
        for cam_data in DEMO_CAMERAS:
            try:
                camera_service.create(cam_data)
            except Exception:
                db.session.rollback()

    if Server.query.count() == 0:
        from app.services.demo.fake_hardware import DEMO_SERVERS
        for srv_data in DEMO_SERVERS:
            try:
                server_service.create(srv_data)
            except Exception:
                db.session.rollback()
