import logging
import urllib3
from datetime import datetime, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)


class HardwareMonitor:
    """Monitor Pelco storage servers via Dell iDRAC Redfish REST API.

    Pelco VX Storage servers are Dell PowerEdge servers with iDRAC.
    Pelco Endura NSM5200 can be monitored via SNMP (fallback).
    """

    def get_server_health(self, server):
        if server.server_type == 'endura':
            return self._get_endura_health(server)
        return self._get_idrac_health(server)

    def get_hdds(self, server):
        if server.server_type == 'endura':
            return self._get_endura_disks(server)
        return self._get_idrac_disks(server)

    def get_psus(self, server):
        if server.server_type == 'endura':
            return []
        return self._get_idrac_psus(server)

    # --- iDRAC Redfish (VX Storage) ---

    def _idrac_get(self, server, path):
        import requests
        idrac_ip = server.idrac_ip or server.ip_address
        idrac_port = server.idrac_port or 443
        username = server.idrac_username or 'root'
        password = server.idrac_password or 'calvin'
        url = f'https://{idrac_ip}:{idrac_port}{path}'
        log.debug('iDRAC GET %s (user=%s)', url, username)
        try:
            resp = requests.get(
                url,
                auth=(username, password),
                verify=False,
                timeout=15,
                headers={'Content-Type': 'application/json'},
            )
            log.debug('iDRAC GET %s -> HTTP %d', url, resp.status_code)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as exc:
            log.error('iDRAC connection failed to %s: %s', idrac_ip, exc)
            raise
        except requests.exceptions.Timeout:
            log.error('iDRAC request timed out: %s', url)
            raise
        except requests.exceptions.HTTPError as exc:
            log.error('iDRAC HTTP error %s %s: %s', url, exc.response.status_code, exc)
            raise

    def _get_idrac_health(self, server):
        now = datetime.now(timezone.utc)
        log.info('Fetching iDRAC health: server=%s ip=%s idrac_ip=%s',
                 server.name, server.ip_address, server.idrac_ip or server.ip_address)
        try:
            system = self._idrac_get(
                server, '/redfish/v1/Systems/System.Embedded.1'
            )
            thermal = self._idrac_get(
                server, '/redfish/v1/Chassis/System.Embedded.1/Thermal'
            )

            inlet_temp = None
            exhaust_temp = None
            for t in thermal.get('Temperatures', []):
                name = (t.get('Name') or '').lower()
                reading = t.get('ReadingCelsius')
                if reading is None:
                    continue
                if 'inlet' in name:
                    inlet_temp = float(reading)
                elif 'exhaust' in name:
                    exhaust_temp = float(reading)

            cpu_usage = system.get('ProcessorSummary', {}).get('CpuUsagePercent')
            memory_usage = system.get('MemorySummary', {}).get('MemoryUsagePercent')
            result = {
                'is_online': True,
                'system_model': system.get('Model'),
                'serial_number': system.get('SerialNumber'),
                'power_state': system.get('PowerState'),
                'health_rollup': system.get('Status', {}).get('HealthRollup'),
                'inlet_temp': inlet_temp,
                'exhaust_temp': exhaust_temp,
                'cpu_usage': cpu_usage,
                'memory_usage': memory_usage,
                'last_checked': now,
            }
            log.info('iDRAC health OK: server=%s model=%s health=%s',
                     server.name, result['system_model'], result['health_rollup'])
            return result
        except Exception as exc:
            log.error('iDRAC health FAILED: server=%s ip=%s — %s: %s',
                      server.name, server.idrac_ip or server.ip_address,
                      type(exc).__name__, exc)
            return {
                'is_online': False,
                'system_model': None,
                'serial_number': None,
                'power_state': None,
                'health_rollup': None,
                'inlet_temp': None,
                'exhaust_temp': None,
                'cpu_usage': None,
                'memory_usage': None,
                'last_checked': now,
            }

    def _get_idrac_disks(self, server):
        disks = []
        log.info('Fetching iDRAC disks: server=%s', server.name)
        try:
            storage = self._idrac_get(
                server, '/redfish/v1/Systems/System.Embedded.1/Storage'
            )
            for member in storage.get('Members', []):
                ctrl_path = member.get('@odata.id')
                if not ctrl_path:
                    continue
                ctrl = self._idrac_get(server, ctrl_path)
                for drive_ref in ctrl.get('Drives', []):
                    drive_path = drive_ref.get('@odata.id')
                    if not drive_path:
                        continue
                    d = self._idrac_get(server, drive_path)
                    cap_bytes = d.get('CapacityBytes')
                    used_bytes = d.get('CapacityUsedBytes')
                    disks.append({
                        'device_name': d.get('Name', d.get('Id', 'Unknown')),
                        'slot': d.get('Id'),
                        'model': d.get('Model'),
                        'serial': d.get('SerialNumber'),
                        'media_type': d.get('MediaType'),
                        'protocol': d.get('Protocol'),
                        'capacity_gb': round(cap_bytes / 1e9, 1) if cap_bytes else None,
                        'used_gb': round(used_bytes / 1e9, 1) if used_bytes is not None else None,
                        'temperature_c': None,
                        'health_status': d.get('Status', {}).get('Health', 'Unknown'),
                        'state': d.get('Status', {}).get('State'),
                        'predicted_life_left': d.get('PredictedMediaLifeLeftPercent'),
                        'power_on_hours': None,
                        'reallocated_sectors': 0,
                        'pending_sectors': 0,
                        'last_checked': datetime.now(timezone.utc),
                    })
        except Exception as exc:
            log.error('iDRAC disks FAILED: server=%s — %s: %s',
                      server.name, type(exc).__name__, exc)
        log.info('iDRAC disks: found %d drive(s) for server=%s', len(disks), server.name)
        return disks

    def _get_idrac_psus(self, server):
        psus = []
        log.info('Fetching iDRAC PSUs: server=%s', server.name)
        try:
            power = self._idrac_get(
                server, '/redfish/v1/Chassis/System.Embedded.1/Power'
            )
            for p in power.get('PowerSupplies', []):
                psus.append({
                    'name': p.get('Name', 'PSU'),
                    'model': p.get('Model'),
                    'health_status': p.get('Status', {}).get('Health', 'Unknown'),
                    'power_watts': p.get('PowerInputWatts'),
                    'capacity_watts': p.get('PowerCapacityWatts'),
                })
        except Exception as exc:
            log.error('iDRAC PSUs FAILED: server=%s — %s: %s',
                      server.name, type(exc).__name__, exc)
        log.info('iDRAC PSUs: found %d PSU(s) for server=%s', len(psus), server.name)
        return psus

    # --- SNMP (Endura NSM5200) ---

    def _get_endura_health(self, server):
        now = datetime.now(timezone.utc)
        try:
            from pysnmp.hlapi import (
                SnmpEngine, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity, getCmd,
            )
            community = server.snmp_community or 'public'

            def snmp_get(oid):
                it = getCmd(
                    SnmpEngine(),
                    CommunityData(community, mpModel=1),
                    UdpTransportTarget((server.ip_address, 161), timeout=10),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                err_ind, err_st, err_idx, var_binds = next(it)
                if err_ind or err_st:
                    return None
                return str(var_binds[0][1]) if var_binds else None

            sys_descr = snmp_get('1.3.6.1.2.1.1.1.0')
            sys_name = snmp_get('1.3.6.1.2.1.1.5.0')

            return {
                'is_online': True,
                'system_model': sys_descr,
                'serial_number': None,
                'power_state': 'On',
                'health_rollup': 'OK',
                'inlet_temp': None,
                'exhaust_temp': None,
                'cpu_usage': None,
                'memory_usage': None,
                'last_checked': now,
            }
        except Exception:
            return {
                'is_online': False,
                'system_model': None,
                'serial_number': None,
                'power_state': None,
                'health_rollup': None,
                'inlet_temp': None,
                'exhaust_temp': None,
                'cpu_usage': None,
                'memory_usage': None,
                'last_checked': now,
            }

    def _get_endura_disks(self, server):
        disks = []
        try:
            from pysnmp.hlapi import (
                SnmpEngine, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity, nextCmd,
            )
            community = server.snmp_community or 'public'

            results = []
            for err_ind, err_st, err_idx, var_binds in nextCmd(
                SnmpEngine(),
                CommunityData(community),
                UdpTransportTarget((server.ip_address, 161), timeout=10),
                ContextData(),
                ObjectType(ObjectIdentity('1.3.6.1.2.1.25.2.3.1.3')),
                lexicographicMode=False,
            ):
                if err_ind or err_st:
                    break
                for vb in var_binds:
                    results.append(str(vb[1]))

            for i, desc in enumerate(results):
                disks.append({
                    'device_name': desc,
                    'slot': None,
                    'model': None,
                    'serial': None,
                    'media_type': 'HDD',
                    'protocol': None,
                    'capacity_gb': None,
                    'used_gb': None,
                    'temperature_c': None,
                    'health_status': 'Unknown',
                    'state': None,
                    'predicted_life_left': None,
                    'power_on_hours': None,
                    'reallocated_sectors': 0,
                    'pending_sectors': 0,
                    'last_checked': datetime.now(timezone.utc),
                })
        except Exception:
            pass
        return disks
