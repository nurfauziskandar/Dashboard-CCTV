"""Dell iDRAC Redfish API Emulator.

Implements the exact endpoints the Dashboard-CCTV queries:
  - /redfish/v1/Systems/System.Embedded.1
  - /redfish/v1/Chassis/System.Embedded.1/Thermal
  - /redfish/v1/Systems/System.Embedded.1/Storage
  - /redfish/v1/Systems/System.Embedded.1/Storage/{ctrl}/Drives/{id}
  - /redfish/v1/Chassis/System.Embedded.1/Power

All data is sourced from real hardware stats via the HardwareMonitor.
"""

import functools
import logging

from flask import Blueprint, jsonify, request, current_app

logger = logging.getLogger(__name__)

bp = Blueprint('redfish', __name__, url_prefix='/redfish/v1')


def require_auth(f):
    """Basic Auth matching configured iDRAC credentials."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        config = current_app.config['APP_CONFIG']
        if not auth or auth.username != config.REDFISH_USERNAME or auth.password != config.REDFISH_PASSWORD:
            return jsonify({'error': 'Unauthorized'}), 401, {
                'WWW-Authenticate': 'Basic realm="iDRAC"'
            }
        return f(*args, **kwargs)
    return decorated


# --- Service Root ---

@bp.route('/')
@require_auth
def service_root():
    return jsonify({
        '@odata.id': '/redfish/v1/',
        '@odata.type': '#ServiceRoot.v1_5_0.ServiceRoot',
        'Id': 'RootService',
        'Name': 'Root Service',
        'RedfishVersion': '1.9.0',
        'Systems': {'@odata.id': '/redfish/v1/Systems'},
        'Chassis': {'@odata.id': '/redfish/v1/Chassis'},
    })


# --- Systems ---

@bp.route('/Systems/System.Embedded.1')
@require_auth
def system_info():
    hw = current_app.config['hw_monitor']
    info = hw.get_system_info()
    health = hw.get_health_rollup()
    mem = hw.get_memory_info()

    return jsonify({
        '@odata.id': '/redfish/v1/Systems/System.Embedded.1',
        '@odata.type': '#ComputerSystem.v1_12_0.ComputerSystem',
        'Id': 'System.Embedded.1',
        'Name': 'System',
        'Model': info['model'],
        'Manufacturer': info.get('model', '').split()[0] if info.get('model') else 'Unknown',
        'SerialNumber': info['serial_number'],
        'HostName': info['hostname'],
        'PowerState': info['power_state'],
        'ProcessorSummary': {
            'Model': info.get('processor', 'Unknown'),
            'Count': __import__('os').cpu_count() or 1,
        },
        'Status': {
            'State': 'Enabled',
            'Health': health,
            'HealthRollup': health,
        },
        'MemorySummary': {
            'TotalSystemMemoryGiB': mem['total_gb'],
            'Status': {'Health': 'OK'},
        },
        'Storage': {
            '@odata.id': '/redfish/v1/Systems/System.Embedded.1/Storage'
        },
    })


# --- Thermal ---

@bp.route('/Chassis/System.Embedded.1/Thermal')
@require_auth
def thermal():
    hw = current_app.config['hw_monitor']
    temps = hw.get_temperatures()

    temperature_entries = []

    if temps.get('inlet') is not None:
        inlet = temps['inlet']
        temperature_entries.append({
            '@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Thermal#/Temperatures/0',
            'MemberId': '0',
            'Name': 'System Board Inlet Temp',
            'ReadingCelsius': inlet,
            'UpperThresholdNonCritical': 42,
            'UpperThresholdCritical': 47,
            'Status': {
                'State': 'Enabled',
                'Health': 'Critical' if inlet > 42 else ('Warning' if inlet > 35 else 'OK'),
            },
            'PhysicalContext': 'Intake',
        })

    if temps.get('exhaust') is not None:
        exhaust = temps['exhaust']
        temperature_entries.append({
            '@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Thermal#/Temperatures/1',
            'MemberId': '1',
            'Name': 'System Board Exhaust Temp',
            'ReadingCelsius': exhaust,
            'UpperThresholdNonCritical': 70,
            'UpperThresholdCritical': 75,
            'Status': {
                'State': 'Enabled',
                'Health': 'Critical' if exhaust > 70 else ('Warning' if exhaust > 55 else 'OK'),
            },
            'PhysicalContext': 'Exhaust',
        })

    return jsonify({
        '@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Thermal',
        '@odata.type': '#Thermal.v1_6_0.Thermal',
        'Id': 'Thermal',
        'Name': 'Thermal',
        'Temperatures': temperature_entries,
    })


# --- Storage Controllers ---

@bp.route('/Systems/System.Embedded.1/Storage')
@require_auth
def storage_controllers():
    return jsonify({
        '@odata.id': '/redfish/v1/Systems/System.Embedded.1/Storage',
        '@odata.type': '#StorageCollection.StorageCollection',
        'Name': 'Storage Collection',
        'Members': [
            {'@odata.id': '/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1'},
        ],
        'Members@odata.count': 1,
    })


@bp.route('/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1')
@require_auth
def storage_controller_detail():
    hw = current_app.config['hw_monitor']
    disks = hw.get_disk_info()

    drives = []
    for i, d in enumerate(disks):
        drive_id = f'Disk.Bay.{i}'
        drives.append({
            '@odata.id': f'/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1/Drives/{drive_id}',
            '_index': i,
        })

    return jsonify({
        '@odata.id': '/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1',
        '@odata.type': '#Storage.v1_8_0.Storage',
        'Id': 'AHCI.Slot.1-1',
        'Name': 'AHCI Controller in Slot 1',
        'StorageControllers': [{
            'MemberId': '0',
            'Model': 'Host Storage Controller',
            'Status': {'Health': 'OK', 'State': 'Enabled'},
        }],
        'Drives': drives,
        'Drives@odata.count': len(drives),
    })


# --- Individual Drives ---

@bp.route('/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1/Drives/<path:drive_id>')
@require_auth
def drive_detail(drive_id):
    hw = current_app.config['hw_monitor']
    disks = hw.get_disk_info()

    # Match by index (Disk.Bay.0, Disk.Bay.1, ...)
    idx = None
    try:
        idx = int(drive_id.replace('Disk.Bay.', ''))
    except (ValueError, AttributeError):
        pass

    if idx is not None and 0 <= idx < len(disks):
        d = disks[idx]
        return jsonify({
            '@odata.id': f'/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.1-1/Drives/{drive_id}',
            '@odata.type': '#Drive.v1_9_0.Drive',
            'Id': drive_id,
            'Name': d['device_name'],
            'Model': d['model'],
            'SerialNumber': d['serial'],
            'MediaType': d['media_type'],
            'Protocol': d['protocol'],
            'CapacityBytes': int(d['capacity_gb'] * 1e9) if d['capacity_gb'] else 0,
            'CapacityUsedBytes': int(d['used_gb'] * 1e9) if d.get('used_gb') is not None else None,
            'BlockSizeBytes': 512,
            'RotationSpeedRPM': 7200 if d['media_type'] == 'HDD' else 0,
            'PredictedMediaLifeLeftPercent': d.get('predicted_life_left'),
            'FailurePredicted': False,
            'Status': {
                'Health': d['health'],
                'State': d['state'],
            },
        })

    return jsonify({'error': 'Drive not found'}), 404


# --- Power ---

@bp.route('/Chassis/System.Embedded.1/Power')
@require_auth
def power():
    hw = current_app.config['hw_monitor']
    psus = hw.get_psu_info()

    power_supplies = []
    for p in psus:
        power_supplies.append({
            '@odata.id': f'/redfish/v1/Chassis/System.Embedded.1/Power#/PowerSupplies/{p["name"]}',
            'MemberId': p['name'],
            'Name': p['name'],
            'Model': p['model'],
            'PowerInputWatts': p['power_watts'],
            'PowerCapacityWatts': p['capacity_watts'],
            'Status': {
                'State': 'Enabled',
                'Health': p['health'],
            },
        })

    return jsonify({
        '@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Power',
        '@odata.type': '#Power.v1_6_0.Power',
        'Id': 'Power',
        'Name': 'Power',
        'PowerSupplies': power_supplies,
    })
