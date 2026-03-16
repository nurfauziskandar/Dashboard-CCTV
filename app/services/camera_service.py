from datetime import datetime, timezone
from app.extensions import db
from app.models.camera import Camera


class CameraService:

    def __init__(self, app):
        self.app = app
        if app.config['DEMO_MODE']:
            from app.services.demo.fake_cameras import FakeCameraAdapter
            self.adapter = FakeCameraAdapter()
        else:
            from app.services.onvif_adapter import ONVIFAdapter
            self.adapter = ONVIFAdapter()

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
            ip_address=data['ip_address'],
            port=data.get('port', 80),
            onvif_username=data.get('onvif_username'),
            onvif_password=data.get('onvif_password'),
            manufacturer=data.get('manufacturer', 'Pelco'),
            model=data.get('model'),
            location_name=data.get('location_name'),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
        )
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
        return camera

    def delete(self, camera_id):
        camera = db.session.get(Camera, camera_id)
        if camera:
            db.session.delete(camera)
            db.session.commit()
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
                except Exception:
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
