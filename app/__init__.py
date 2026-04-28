import os
import logging
from datetime import timedelta
from logging.handlers import RotatingFileHandler
import click
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
            stream_service = StreamService(
                target_fps=app.config.get('STREAM_FPS'),
                max_width=app.config.get('STREAM_MAX_WIDTH'),
                jpeg_quality=app.config.get('STREAM_JPEG_QUALITY'),
            )

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

        # Two-way sync: push dashboard rows to storage (recorder resume)
        # AND pull storage rows that the dashboard doesn't know about
        # (camera was added directly via storage UI).
        _sync_cameras_with_storage(app, camera_service, storage_client)

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

        # Two-way sync every 60s — fast enough that adding a camera on the
        # storage UI shows up on the dashboard within ~1 min, slow enough
        # not to thrash either side.
        @scheduler.task('interval', id='sync_storage', seconds=60)
        def sync_storage():
            with app.app_context():
                _sync_cameras_with_storage(app, camera_service, storage_client)

        scheduler.start()

        # Capture an initial snapshot for today on startup so summary shows
        # data even before the first midnight tick.
        try:
            snapshot_service.capture()
        except Exception as exc:
            app.logger.warning('Initial snapshot capture failed: %s', exc)

    @app.cli.command('seed-sample')
    @click.option('--cameras/--no-cameras', default=True, help='Seed sample cameras')
    @click.option('--servers/--no-servers', default=True, help='Seed sample servers')
    @click.option('--snapshots/--no-snapshots', default=True, help='Seed 30-day historical snapshots')
    @click.option('--days', default=30, show_default=True, help='Number of historical days to backfill')
    def seed_sample(cameras, servers, snapshots, days):
        """Seed sample cameras, servers, and historical snapshots for reports.

        Safe to run in production — skips entries that already exist by name.
        Snapshots are only inserted for dates that have no existing rows.
        """
        import random
        from datetime import date, timedelta
        from app.extensions import db
        from app.services.camera_service import CameraService
        from app.services.server_service import ServerService
        from app.models.camera import Camera
        from app.models.server import Server
        from app.models.snapshot import StatusSnapshot

        with app.app_context():
            cam_svc = CameraService(app)
            srv_svc = ServerService(app)
            cam_count = 0
            srv_count = 0

            if cameras:
                from app.services.demo.fake_cameras import DEMO_CAMERAS
                existing_names = {c.name for c in Camera.query.all()}
                for cam_data in DEMO_CAMERAS:
                    if cam_data['name'] in existing_names:
                        click.echo(f'  skip camera: {cam_data["name"]} (already exists)')
                        continue
                    try:
                        cam_svc.create(cam_data)
                        cam_count += 1
                        click.echo(f'  + camera: {cam_data["name"]}')
                    except Exception as exc:
                        db.session.rollback()
                        click.echo(f'  ! camera {cam_data["name"]} failed: {exc}', err=True)

            if servers:
                from app.services.demo.fake_hardware import DEMO_SERVERS
                existing_names = {s.name for s in Server.query.all()}
                for srv_data in DEMO_SERVERS:
                    if srv_data['name'] in existing_names:
                        click.echo(f'  skip server: {srv_data["name"]} (already exists)')
                        continue
                    try:
                        srv_svc.create(srv_data)
                        srv_count += 1
                        click.echo(f'  + server: {srv_data["name"]}')
                    except Exception as exc:
                        db.session.rollback()
                        click.echo(f'  ! server {srv_data["name"]} failed: {exc}', err=True)

            click.echo(f'Done. Added {cam_count} camera(s), {srv_count} server(s).')

            if not snapshots:
                return

            # Backfill historical snapshots
            all_servers = Server.query.all()
            if not all_servers:
                click.echo('  No servers in DB — skipping snapshot seed.')
                return

            cam_total = Camera.query.count()
            today = date.today()
            snap_inserted = 0

            # Stable per-server random seed so data looks consistent day-to-day
            srv_base = {srv.id: hash(srv.name) & 0xFFFF for srv in all_servers}

            for days_ago in range(days, -1, -1):
                d = today - timedelta(days=days_ago)

                # Skip if aggregate row already exists for this date
                if StatusSnapshot.query.filter_by(snapshot_date=d, server_id=None).first():
                    continue

                # Camera aggregate
                cam_active = random.randint(max(0, int(cam_total * 0.75)), cam_total)
                agg = StatusSnapshot(snapshot_date=d, server_id=None)
                agg.cam_total = cam_total
                agg.cam_active = cam_active
                agg.cam_inactive = cam_total - cam_active
                db.session.add(agg)

                for srv in all_servers:
                    if StatusSnapshot.query.filter_by(snapshot_date=d, server_id=srv.id).first():
                        continue
                    rng = random.Random(srv_base[srv.id] + days_ago)
                    is_online = rng.random() > 0.04
                    health = rng.choices(
                        ['OK', 'OK', 'Warning', 'Critical'],
                        weights=[14, 4, 1, 0.3],
                    )[0]
                    hdd_total = rng.randint(8, 16)
                    hdd_alerts = 0 if health == 'OK' else rng.randint(1, 3)
                    row = StatusSnapshot(snapshot_date=d, server_id=srv.id)
                    row.server_name = srv.name
                    row.is_online = is_online
                    row.health_rollup = health
                    row.inlet_temp = round(rng.uniform(21.0, 34.0), 1) if is_online else None
                    row.cpu_usage = round(rng.uniform(12.0, 68.0), 1) if is_online else None
                    row.memory_usage = round(rng.uniform(30.0, 82.0), 1) if is_online else None
                    row.hdd_total = hdd_total
                    row.hdd_alerts = hdd_alerts
                    row.cam_total = cam_total
                    row.cam_active = cam_active
                    row.cam_inactive = cam_total - cam_active
                    db.session.add(row)

                db.session.commit()
                snap_inserted += 1

            click.echo(f'Snapshots: backfilled {snap_inserted} day(s) ({days} days range).')

    return app


def _sync_cameras_with_storage(app, camera_service, storage_client):
    """Bi-directional reconcile between dashboard DB and storage server.

    Push: dashboard row not registered on storage → POST it
    Pull: storage entry not in dashboard DB → create a Camera row from
          the metadata storage already has

    Run on startup + every 60s. Best-effort; storage offline = silent skip.
    """
    if not storage_client or not storage_client.enabled:
        return
    try:
        storage_cams = storage_client.list_cameras()
    except Exception as exc:
        app.logger.warning('Storage sync skipped — list_cameras failed: %s', exc)
        return

    from app.services.storage_client import slugify
    storage_by_slug = {
        c.get('slug') or slugify(c.get('name', '')): c
        for c in storage_cams if c.get('name') and c.get('rtsp_uri')
    }
    storage_slugs = set(storage_by_slug)

    db_cameras = camera_service.get_all()
    db_by_slug = {slugify(cam.name): cam for cam in db_cameras}
    db_slugs = set(db_by_slug)

    # Push dashboard → storage
    pushed = 0
    from app.services.camera_service import _camera_metadata
    for cam in db_cameras:
        if not cam.stream_uri:
            continue
        if slugify(cam.name) in storage_slugs:
            continue
        if storage_client.register_camera(
            cam.name, cam.stream_uri, metadata=_camera_metadata(cam),
        ):
            pushed += 1

    # Pull storage → dashboard
    # Case A: slug not in DB at all → create
    # Case B: slug in DB but stream_uri empty → patch with storage data
    pulled = 0
    patched = 0
    for slug in storage_slugs:
        cam_data = storage_by_slug[slug]
        data = {
            'add_mode': 'rtsp',
            'name': cam_data['name'],
            'stream_uri': cam_data['rtsp_uri'],
            'ip_address': cam_data.get('ip_address') or '',
            'port': cam_data.get('port') or 554,
            'manufacturer': cam_data.get('manufacturer') or 'Pelco',
            'model': cam_data.get('model'),
            'location_name': cam_data.get('location_name'),
            'onvif_username': cam_data.get('onvif_username'),
            'onvif_password': cam_data.get('onvif_password'),
        }
        if cam_data.get('latitude') is not None:
            data['latitude'] = cam_data['latitude']
        if cam_data.get('longitude') is not None:
            data['longitude'] = cam_data['longitude']

        if slug not in db_slugs:
            try:
                camera_service.create(data)
                pulled += 1
            except Exception:
                app.logger.exception('Storage sync: failed to pull %s', slug)
        elif not db_by_slug[slug].stream_uri:
            try:
                camera_service.update(db_by_slug[slug].id, data)
                patched += 1
            except Exception:
                app.logger.exception('Storage sync: failed to patch %s', slug)

    if pushed or pulled or patched:
        app.logger.info('Storage sync: pushed=%d pulled=%d patched=%d', pushed, pulled, patched)


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
