import logging
from datetime import datetime, timezone
from app.extensions import db
from app.models.server import Server, HDD, PSU

log = logging.getLogger(__name__)


class ServerService:

    def __init__(self, app):
        self.app = app
        if app.config['DEMO_MODE']:
            from app.services.demo.fake_hardware import FakeHardwareMonitor
            self.monitor = FakeHardwareMonitor()
            log.info('ServerService: using FakeHardwareMonitor (DEMO_MODE)')
        else:
            from app.services.hardware_monitor import HardwareMonitor
            self.monitor = HardwareMonitor()
            log.info('ServerService: using HardwareMonitor (production)')

    def get_all(self):
        return Server.query.order_by(Server.name).all()

    def get_by_id(self, server_id):
        return db.session.get(Server, server_id)

    def create(self, data):
        log.info('Creating server: name=%s ip=%s type=%s idrac_ip=%s',
                 data.get('name'), data.get('ip_address'),
                 data.get('server_type'), data.get('idrac_ip'))
        server = Server(
            name=data['name'],
            ip_address=data['ip_address'],
            description=data.get('description'),
            server_type=data.get('server_type', 'vxstorage'),
            idrac_ip=data.get('idrac_ip'),
            idrac_port=int(data['idrac_port']) if data.get('idrac_port') else 443,
            idrac_username=data.get('idrac_username'),
            idrac_password=data.get('idrac_password'),
            snmp_community=data.get('snmp_community', 'public'),
            vms_port=int(data['vms_port']) if data.get('vms_port') else 8116,
            vms_username=data.get('vms_username'),
            vms_password=data.get('vms_password'),
        )
        db.session.add(server)
        db.session.commit()
        log.info('Server saved to DB with id=%d, running initial refresh...', server.id)
        self._refresh_server(server)
        return server

    def update(self, server_id, data):
        server = db.session.get(Server, server_id)
        if not server:
            return None
        server.name = data.get('name', server.name)
        server.ip_address = data.get('ip_address', server.ip_address)
        server.description = data.get('description', server.description)
        server.server_type = data.get('server_type', server.server_type)
        server.idrac_ip = data.get('idrac_ip') or server.idrac_ip
        server.idrac_port = int(data['idrac_port']) if data.get('idrac_port') else server.idrac_port
        if data.get('idrac_username'):
            server.idrac_username = data['idrac_username']
        if data.get('idrac_password'):
            server.idrac_password = data['idrac_password']
        server.snmp_community = data.get('snmp_community', server.snmp_community)
        if data.get('vms_port'):
            server.vms_port = int(data['vms_port'])
        if data.get('vms_username'):
            server.vms_username = data['vms_username']
        if data.get('vms_password'):
            server.vms_password = data['vms_password']
        db.session.commit()
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
            log.warning('refresh_one: server id=%d not found', server_id)
            return None
        log.info('Refreshing server id=%d name=%s', server.id, server.name)
        self._refresh_server(server)
        return server

    def _refresh_vms_server(self, server):
        from app.services.axxon_adapter import AxxonNextAdapter
        adapter = AxxonNextAdapter()
        port = server.vms_port or 8116
        result = adapter.test_connection(
            server.ip_address, port, server.vms_username, server.vms_password
        )
        server.is_online = result['ok']
        server.last_checked = datetime.now(timezone.utc)
        if result['ok']:
            cameras = adapter.list_cameras(
                server.ip_address, port, server.vms_username, server.vms_password
            )
            server.vms_cameras_total = len(cameras)
            server.vms_cameras_active = sum(1 for c in cameras if c.get('is_active'))
            server.health_rollup = 'OK'
            server.power_state = 'On'
        else:
            server.health_rollup = 'Critical'
            server.power_state = None
        db.session.commit()

    def _refresh_server(self, server):
        if server.server_type == 'axxon':
            self._refresh_vms_server(server)
            return
        log.debug('_refresh_server: id=%d ip=%s type=%s', server.id, server.ip_address, server.server_type)
        health = self.monitor.get_server_health(server)
        log.debug('_refresh_server: health result is_online=%s health_rollup=%s',
                  health.get('is_online'), health.get('health_rollup'))
        is_online = health.get('is_online', False)
        server.is_online = is_online
        server.last_checked = health.get('last_checked', datetime.now(timezone.utc))

        if not is_online:
            # Server offline — wipe live metrics so UI doesn't show stale values.
            # Keep identity fields (model/serial) so the row still labels correctly.
            server.power_state = None
            server.health_rollup = None
            server.inlet_temp = None
            server.exhaust_temp = None
            server.cpu_usage = None
            server.memory_usage = None
            # Clear HDD stats too (mark as Unknown)
            for hdd in server.hdds:
                hdd.health_status = 'Unknown'
                hdd.temperature_c = None
                hdd.state = None
                hdd.last_checked = datetime.now(timezone.utc)
            for psu in server.psus:
                psu.health_status = 'Unknown'
                psu.power_watts = None
                psu.last_checked = datetime.now(timezone.utc)
            db.session.commit()
            return

        server.system_model = health.get('system_model') or server.system_model
        server.serial_number = health.get('serial_number') or server.serial_number
        server.power_state = health.get('power_state')
        server.health_rollup = health.get('health_rollup')
        server.inlet_temp = health.get('inlet_temp')
        server.exhaust_temp = health.get('exhaust_temp')
        server.cpu_usage = health.get('cpu_usage')
        server.memory_usage = health.get('memory_usage')

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
            log.debug('poll_all: polling %d server(s)', len(servers))
            for server in servers:
                try:
                    self._refresh_server(server)
                except Exception as exc:
                    log.error('poll_all: error refreshing server id=%d name=%s: %s',
                              server.id, server.name, exc, exc_info=True)
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
