"""Daily status snapshots for summary report.

A row per (snapshot_date, server_id?) — server snapshots are individual rows;
the camera roll-up is stored once per date in a row with server_id=NULL.
"""
from datetime import datetime, timezone, date
from app.extensions import db


class StatusSnapshot(db.Model):
    __tablename__ = 'status_snapshot'

    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, nullable=False, index=True)

    # NULL = aggregate row (camera totals, no specific server)
    server_id = db.Column(db.Integer, nullable=True, index=True)
    server_name = db.Column(db.String(120))

    # Server fields (nullable for aggregate row)
    is_online = db.Column(db.Boolean)
    health_rollup = db.Column(db.String(20))
    inlet_temp = db.Column(db.Float)
    cpu_usage = db.Column(db.Float)
    memory_usage = db.Column(db.Float)
    hdd_total = db.Column(db.Integer)
    hdd_alerts = db.Column(db.Integer)

    # Camera totals (filled on every row for convenience)
    cam_total = db.Column(db.Integer)
    cam_active = db.Column(db.Integer)
    cam_inactive = db.Column(db.Integer)

    created_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint('snapshot_date', 'server_id', name='uq_snapshot_date_server'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'snapshot_date': self.snapshot_date.isoformat() if self.snapshot_date else None,
            'server_id': self.server_id,
            'server_name': self.server_name,
            'is_online': self.is_online,
            'health_rollup': self.health_rollup,
            'inlet_temp': self.inlet_temp,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'hdd_total': self.hdd_total,
            'hdd_alerts': self.hdd_alerts,
            'cam_total': self.cam_total,
            'cam_active': self.cam_active,
            'cam_inactive': self.cam_inactive,
        }
