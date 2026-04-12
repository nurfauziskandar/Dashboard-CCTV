from datetime import datetime, timezone
from app.extensions import db


class Server(db.Model):
    __tablename__ = 'server'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False, unique=True)
    description = db.Column(db.Text)
    server_type = db.Column(db.String(30), default='vxstorage')  # vxstorage, endura, other
    system_model = db.Column(db.String(120))
    serial_number = db.Column(db.String(80))
    power_state = db.Column(db.String(20))
    health_rollup = db.Column(db.String(20))

    # iDRAC connection (for VxStorage Dell servers)
    idrac_ip = db.Column(db.String(45))
    idrac_port = db.Column(db.Integer, default=443)
    idrac_username = db.Column(db.String(80))
    idrac_password = db.Column(db.String(255))

    # SNMP connection (for Endura)
    snmp_community = db.Column(db.String(80), default='public')

    # System metrics
    inlet_temp = db.Column(db.Float)
    exhaust_temp = db.Column(db.Float)
    cpu_usage = db.Column(db.Float)
    memory_usage = db.Column(db.Float)
    is_online = db.Column(db.Boolean, default=True)
    last_checked = db.Column(db.DateTime)
    created_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    hdds = db.relationship('HDD', backref='server', cascade='all, delete-orphan')
    psus = db.relationship('PSU', backref='server', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'ip_address': self.ip_address,
            'description': self.description,
            'server_type': self.server_type,
            'system_model': self.system_model,
            'serial_number': self.serial_number,
            'power_state': self.power_state,
            'health_rollup': self.health_rollup,
            'inlet_temp': self.inlet_temp,
            'exhaust_temp': self.exhaust_temp,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'is_online': self.is_online,
            'last_checked': self.last_checked.isoformat() if self.last_checked else None,
            'hdds': [h.to_dict() for h in self.hdds],
            'psus': [p.to_dict() for p in self.psus],
        }

    @property
    def inlet_temp_status(self):
        if self.inlet_temp is None:
            return 'unknown'
        if self.inlet_temp < 35:
            return 'normal'
        if self.inlet_temp < 42:
            return 'warning'
        return 'critical'

    @property
    def exhaust_temp_status(self):
        if self.exhaust_temp is None:
            return 'unknown'
        if self.exhaust_temp < 55:
            return 'normal'
        if self.exhaust_temp < 70:
            return 'warning'
        return 'critical'

    @property
    def type_label(self):
        labels = {
            'vxstorage': 'Pelco VX Storage',
            'endura': 'Pelco Endura',
            'other': 'Other',
        }
        return labels.get(self.server_type, self.server_type)


class HDD(db.Model):
    __tablename__ = 'hdd'

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(
        db.Integer, db.ForeignKey('server.id', ondelete='CASCADE'), nullable=False
    )
    device_name = db.Column(db.String(80), nullable=False)
    slot = db.Column(db.String(20))
    model = db.Column(db.String(120))
    serial = db.Column(db.String(80))
    media_type = db.Column(db.String(20))   # HDD, SSD
    protocol = db.Column(db.String(20))     # SATA, SAS, NVMe
    capacity_gb = db.Column(db.Float)
    used_gb = db.Column(db.Float)
    temperature_c = db.Column(db.Float)
    health_status = db.Column(db.String(20), default='Unknown')  # OK, Warning, Critical
    state = db.Column(db.String(30))        # Enabled, StandbyOffline, etc.
    predicted_life_left = db.Column(db.Integer)  # percentage, for SSDs
    power_on_hours = db.Column(db.Integer)
    reallocated_sectors = db.Column(db.Integer, default=0)
    pending_sectors = db.Column(db.Integer, default=0)
    last_checked = db.Column(db.DateTime)
    created_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'device_name': self.device_name,
            'slot': self.slot,
            'model': self.model,
            'serial': self.serial,
            'media_type': self.media_type,
            'protocol': self.protocol,
            'capacity_gb': self.capacity_gb,
            'used_gb': self.used_gb,
            'temperature_c': self.temperature_c,
            'health_status': self.health_status,
            'state': self.state,
            'predicted_life_left': self.predicted_life_left,
            'power_on_hours': self.power_on_hours,
            'reallocated_sectors': self.reallocated_sectors,
            'pending_sectors': self.pending_sectors,
            'last_checked': self.last_checked.isoformat() if self.last_checked else None,
        }

    @property
    def usage_percent(self):
        if self.capacity_gb and self.used_gb:
            return round((self.used_gb / self.capacity_gb) * 100, 1)
        return 0

    @property
    def temp_status(self):
        if self.temperature_c is None:
            return 'unknown'
        if self.temperature_c < 40:
            return 'normal'
        if self.temperature_c < 50:
            return 'warning'
        return 'critical'


class PSU(db.Model):
    __tablename__ = 'psu'

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(
        db.Integer, db.ForeignKey('server.id', ondelete='CASCADE'), nullable=False
    )
    name = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(120))
    health_status = db.Column(db.String(20), default='Unknown')
    power_watts = db.Column(db.Float)
    capacity_watts = db.Column(db.Float)
    last_checked = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'name': self.name,
            'model': self.model,
            'health_status': self.health_status,
            'power_watts': self.power_watts,
            'capacity_watts': self.capacity_watts,
        }

    @property
    def usage_percent(self):
        if self.capacity_watts and self.power_watts:
            return round((self.power_watts / self.capacity_watts) * 100, 1)
        return 0
