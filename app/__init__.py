import os
from flask import Flask
from config import config_map


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_map.get(config_name, config_map['default']))

    os.makedirs(app.instance_path, exist_ok=True)

    # Init extensions
    from app.extensions import db, scheduler
    db.init_app(app)

    # Import models so SQLAlchemy knows about them
    from app.models import Camera, Server, HDD, PSU  # noqa: F401

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

        app.config['camera_service'] = camera_service
        app.config['server_service'] = server_service
        app.config['stream_service'] = stream_service

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

        scheduler.start()

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
