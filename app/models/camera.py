from datetime import datetime, timezone
from app.extensions import db


class Camera(db.Model):
    __tablename__ = 'camera'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False, unique=True)
    port = db.Column(db.Integer, nullable=False, default=80)
    onvif_username = db.Column(db.String(80))
    onvif_password = db.Column(db.String(255))
    manufacturer = db.Column(db.String(80), default='Pelco')
    model = db.Column(db.String(80))
    firmware = db.Column(db.String(80))
    location_name = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    stream_uri = db.Column(db.String(500))
    snapshot_uri = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'ip_address': self.ip_address,
            'port': self.port,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'firmware': self.firmware,
            'location_name': self.location_name,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'stream_uri': self.stream_uri,
            'snapshot_uri': self.snapshot_uri,
            'is_active': self.is_active,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat(),
        }
