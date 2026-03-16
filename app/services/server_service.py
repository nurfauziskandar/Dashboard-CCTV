from datetime import datetime, timezone
from app.extensions import db
from app.models.server import Server, HDD, PSU


class ServerService:

    def __init__(self, app):
        self.app = app
        if app.config['DEMO_MODE']:
            from app.services.demo.fake_hardware import FakeHardwareMonitor
            self.monitor = FakeHardwareMonitor()
        else:
            from app.services.hardware_monitor import HardwareMonitor
            self.monitor = HardwareMonitor()

    def get_all(self):
        return Server.query.order_by(Server.name).all()

    def get_by_id(self, server_id):
        return db.session.get(Server, server_id)

    def create(self, data):
        server = Server(
            name=data['name'],
            ip_address=data['ip_address'],
            description=data.get('description'),
            server_type=data.get('server_type', 'vxstorage'),
            idrac_ip=data.get('idrac_ip'),
            idrac_username=data.get('idrac_username'),
            idrac_password=data.get('idrac_password'),
            snmp_community=data.get('snmp_community', 'public'),
        )
        db.session.add(server)
        db.session.commit()
        self._refresh_server(server)
        return server

    def delete(self, server_id):
        server = db.session.get(Server, server_id)
        if server:
            db.session.delete(server)
            db.session.commit()
            return True
        return False

    def refresh_one(self, server_id):
        server = db.session.get(Server, server_id)
        if not server:
            return None
        self._refresh_server(server)
        return server

    def _refresh_server(self, server):
        health = self.monitor.get_server_health(server)
        server.system_model = health.get('system_model') or server.system_model
        server.serial_number = health.get('serial_number') or server.serial_number
        server.power_state = health.get('power_state')
        server.health_rollup = health.get('health_rollup')
        server.inlet_temp = health.get('inlet_temp')
        server.exhaust_temp = health.get('exhaust_temp')
        server.cpu_usage = health.get('cpu_usage')
        server.memory_usage = health.get('memory_usage')
        server.is_online = health.get('is_online', True)
        server.last_checked = health.get('last_checked', datetime.now(timezone.utc))

        # Update HDDs
        hdd_data_list = self.monitor.get_hdds(server)
        existing_hdds = {h.device_name: h for h in server.hdds}

        for hdd_data in hdd_data_list:
            dev = hdd_data['device_name']
            if dev in existing_hdds:
                hdd = existing_hdds[dev]
            else:
                hdd = HDD(server_id=server.id, device_name=dev)
                db.session.add(hdd)

            hdd.slot = hdd_data.get('slot')
            hdd.model = hdd_data.get('model')
            hdd.serial = hdd_data.get('serial')
            hdd.media_type = hdd_data.get('media_type')
            hdd.protocol = hdd_data.get('protocol')
            hdd.capacity_gb = hdd_data.get('capacity_gb')
            hdd.used_gb = hdd_data.get('used_gb')
            hdd.temperature_c = hdd_data.get('temperature_c')
            hdd.health_status = hdd_data.get('health_status', 'Unknown')
            hdd.state = hdd_data.get('state')
            hdd.predicted_life_left = hdd_data.get('predicted_life_left')
            hdd.power_on_hours = hdd_data.get('power_on_hours')
            hdd.reallocated_sectors = hdd_data.get('reallocated_sectors', 0)
            hdd.pending_sectors = hdd_data.get('pending_sectors', 0)
            hdd.last_checked = hdd_data.get('last_checked', datetime.now(timezone.utc))

        # Update PSUs
        psu_data_list = self.monitor.get_psus(server)
        existing_psus = {p.name: p for p in server.psus}

        for psu_data in psu_data_list:
            pname = psu_data['name']
            if pname in existing_psus:
                psu = existing_psus[pname]
            else:
                psu = PSU(server_id=server.id, name=pname)
                db.session.add(psu)

            psu.model = psu_data.get('model')
            psu.health_status = psu_data.get('health_status', 'Unknown')
            psu.power_watts = psu_data.get('power_watts')
            psu.capacity_watts = psu_data.get('capacity_watts')
            psu.last_checked = datetime.now(timezone.utc)

        db.session.commit()

    def poll_all(self):
        with self.app.app_context():
            servers = Server.query.all()
            for server in servers:
                try:
                    self._refresh_server(server)
                except Exception:
                    server.is_online = False
                    server.last_checked = datetime.now(timezone.utc)
            db.session.commit()

    def get_alert_count(self):
        return HDD.query.filter(
            HDD.health_status.in_(['Critical', 'Warning'])
        ).count()

    def get_summary(self):
        servers = Server.query.all()
        total_hdds = HDD.query.count()
        alerts = self.get_alert_count()
        return {
            'total_servers': len(servers),
            'online': sum(1 for s in servers if s.is_online),
            'offline': sum(1 for s in servers if not s.is_online),
            'total_hdds': total_hdds,
            'hdd_alerts': alerts,
        }
