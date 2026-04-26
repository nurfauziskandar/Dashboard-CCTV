import logging
from datetime import datetime, timezone
from app.extensions import db
from app.models.camera import Camera

log = logging.getLogger(__name__)


class CameraService:

    def __init__(self, app):
        self.app = app
        self.storage_client = None  # injected by app factory
        if app.config['DEMO_MODE']:
            from app.services.demo.fake_cameras import FakeCameraAdapter
            self.adapter = FakeCameraAdapter()
            log.info('CameraService: using FakeCameraAdapter (DEMO_MODE)')
        else:
            from app.services.onvif_adapter import ONVIFAdapter
            self.adapter = ONVIFAdapter()
            log.info('CameraService: using ONVIFAdapter (production)')

    def get_all(self, status=None):
        query = Camera.query
        if status == 'active':
            query = query.filter_by(is_active=True)
        elif status == 'inactive':
            query = query.filter_by(is_active=False)
        return query.order_by(Camera.name).all()

    def get_by_id(self, camera_id):
        return db.session.get(Camera, camera_id)

    def create(self, data):
        camera = Camera(
            name=data['name'],
            ip_address=data.get('ip_address', ''),
            port=data.get('port', 80),
            onvif_username=data.get('onvif_username'),
            onvif_password=data.get('onvif_password'),
            manufacturer=data.get('manufacturer', 'Pelco'),
            model=data.get('model'),
            location_name=data.get('location_name'),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
        )

        if data.get('add_mode') == 'rtsp':
            camera.stream_uri = data.get('stream_uri')
            camera.is_active = bool(camera.stream_uri)
        else:
            probe_result = self.adapter.probe(
                camera.ip_address, camera.port,
                camera.onvif_username, camera.onvif_password
            )
            camera.is_active = probe_result['is_active']
            camera.stream_uri = probe_result.get('stream_uri')
            camera.snapshot_uri = probe_result.get('snapshot_uri')
            camera.last_seen = probe_result.get('last_seen')
            if probe_result.get('model'):
                camera.model = probe_result['model']
            if probe_result.get('firmware'):
                camera.firmware = probe_result['firmware']

        db.session.add(camera)
        db.session.commit()

        # Auto-register to storage backend (best-effort, non-blocking failure)
        if self.storage_client and self.storage_client.enabled and camera.stream_uri:
            ok = self.storage_client.register_camera(camera.name, camera.stream_uri)
            if ok:
                log.info('Camera %s registered to storage', camera.name)
            else:
                log.warning('Camera %s storage register failed', camera.name)

        return camera

    def delete(self, camera_id):
        camera = db.session.get(Camera, camera_id)
        if camera:
            name = camera.name
            db.session.delete(camera)
            db.session.commit()
            if self.storage_client and self.storage_client.enabled:
                self.storage_client.unregister_camera(name)
            return True
        return False

    def refresh_one(self, camera_id):
        camera = db.session.get(Camera, camera_id)
        if not camera:
            return None
        result = self.adapter.probe(
            camera.ip_address, camera.port,
            camera.onvif_username, camera.onvif_password
        )
        camera.is_active = result['is_active']
        camera.stream_uri = result.get('stream_uri')
        camera.snapshot_uri = result.get('snapshot_uri')
        camera.last_seen = result.get('last_seen')
        camera.updated_at = datetime.now(timezone.utc)
        if result.get('model'):
            camera.model = result['model']
        if result.get('firmware'):
            camera.firmware = result['firmware']
        db.session.commit()
        return camera

    def poll_all(self):
        with self.app.app_context():
            cameras = Camera.query.all()
            for camera in cameras:
                try:
                    result = self.adapter.probe(
                        camera.ip_address, camera.port,
                        camera.onvif_username, camera.onvif_password
                    )
                    camera.is_active = result['is_active']
                    camera.stream_uri = result.get('stream_uri')
                    camera.snapshot_uri = result.get('snapshot_uri')
                    camera.last_seen = result.get('last_seen')
                    camera.updated_at = datetime.now(timezone.utc)
                except Exception as exc:
                    log.error('poll_all: error probing camera id=%d name=%s ip=%s: %s',
                              camera.id, camera.name, camera.ip_address, exc, exc_info=True)
                    camera.is_active = False
                    camera.updated_at = datetime.now(timezone.utc)
            db.session.commit()

    def discover(self):
        return self.adapter.discover()

    def get_counts(self):
        total = Camera.query.count()
        active = Camera.query.filter_by(is_active=True).count()
        return {
            'total': total,
            'active': active,
            'inactive': total - active,
        }
