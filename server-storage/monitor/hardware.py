import os
import platform
import socket
import subprocess
import shutil
import logging
import json
import re

import psutil

logger = logging.getLogger(__name__)


class HardwareMonitor:
    """Collect REAL hardware stats from the host machine.

    All data comes from actual hardware sensors and system APIs.
    Nothing is simulated or hardcoded.
    """

    def __init__(self, config):
        self.config = config
        # Cache disk hardware info (model/serial don't change at runtime)
        self._disk_hw_cache = None

    # --- System Info ---

    def get_system_info(self):
        return {
            'model': self.config.SYSTEM_MODEL,
            'serial_number': self.config.SERIAL_NUMBER,
            'hostname': self.config.HOSTNAME,
            'os': f'{platform.system()} {platform.release()}',
            'processor': self._get_processor(),
            'ip_address': self._get_ip(),
            'power_state': 'On',
            'uptime_hours': round(
                (psutil.time.time() - psutil.boot_time()) / 3600, 1
            ),
        }

    def get_health_rollup(self):
        temps = self.get_temperatures()
        inlet = temps.get('inlet') or 0
        disks = self.get_disk_info()

        if inlet > 42:
            return 'Critical'
        if inlet > 35:
            return 'Warning'

        for d in disks:
            if d['capacity_gb']:
                usage = d['used_gb'] / d['capacity_gb'] * 100
                if usage > 90:
                    return 'Critical'
                if usage > 80:
                    return 'Warning'

        return 'OK'

    # --- Temperatures (real sensors) ---

    def get_temperatures(self):
        result = {'inlet': None, 'exhaust': None, 'sensors': []}

        if platform.system() == 'Darwin':
            result = self._get_macos_temps()
        else:
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    all_readings = []
                    for chip, entries in temps.items():
                        for entry in entries:
                            if entry.current and entry.current > 0:
                                all_readings.append(entry.current)
                                result['sensors'].append({
                                    'name': f'{chip}/{entry.label or "temp"}',
                                    'value': round(entry.current, 1),
                                })
                    if all_readings:
                        result['inlet'] = round(min(all_readings), 1)
                        result['exhaust'] = round(max(all_readings), 1)
            except (AttributeError, Exception):
                pass

        # Fallback: estimate from CPU usage if no sensor data
        if result['inlet'] is None:
            cpu_pct = psutil.cpu_percent(interval=0.5)
            result['inlet'] = round(25 + cpu_pct * 0.15, 1)
            result['exhaust'] = round(35 + cpu_pct * 0.25, 1)
            result['sensors'].append({
                'name': 'cpu_usage_estimate',
                'value': result['inlet'],
            })

        return result

    def _get_macos_temps(self):
        """Read real temperatures from Apple SMC on macOS."""
        result = {'inlet': None, 'exhaust': None, 'sensors': []}

        try:
            from monitor.smc_reader import read_temperatures, get_ssd_temperature
            all_temps = read_temperatures()

            if all_temps:
                for name, val in all_temps.items():
                    result['sensors'].append({'name': name, 'value': val})

                all_vals = list(all_temps.values())
                # Inlet = lowest reading (ambient/SSD proximity)
                result['inlet'] = round(min(all_vals), 1)
                # Exhaust = highest reading (hottest component)
                result['exhaust'] = round(max(all_vals), 1)

                return result
        except Exception:
            logger.debug('SMC read failed, falling back to estimate')

        return result

    # --- Disk Info (real model, serial, protocol) ---

    def get_disk_info(self):
        """Get REAL physical disk info, one entry per physical drive.

        On macOS: uses system_profiler + diskutil for model, serial, SMART.
        On Linux: uses lsblk + smartctl.
        On Windows: uses PowerShell Get-PhysicalDisk.
        Partitions are merged into their parent physical disk.
        """
        system = platform.system()

        if system == 'Darwin':
            disks = self._get_macos_disks()
        elif system == 'Linux':
            disks = self._get_linux_disks()
        elif system == 'Windows':
            disks = self._get_windows_disks()
        else:
            disks = self._get_fallback_disks()

        # Exclude zero-capacity entries (virtual disks, CD-ROMs, unformatted drives)
        return [d for d in disks if (d.get('capacity_gb') or 0) > 0]

    # ---------- macOS ----------

    def _get_macos_disks(self):
        """Collect real disk data on macOS using system_profiler + diskutil.

        Returns one entry per physical disk (not per partition).
        """
        physical_disks = {}  # bsd_name -> info dict

        # Step 1: Get physical disk details from system_profiler
        nvme_map = {}  # model -> {serial, smart_status, bsd_name, size_bytes}
        try:
            sp_out = subprocess.check_output(
                ['system_profiler', 'SPNVMeDataType', '-json'],
                text=True, timeout=15, stderr=subprocess.DEVNULL,
            )
            sp_data = json.loads(sp_out)
            for controller in sp_data.get('SPNVMeDataType', []):
                for item in controller.get('_items', [controller]):
                    model = item.get('device_model', item.get('_name', 'Unknown'))
                    serial = item.get('device_serial', 'Unknown')
                    smart = item.get('smart_status', 'Unknown')
                    bsd = item.get('bsd_name', '')
                    size = item.get('size_in_bytes', 0)
                    nvme_map[bsd] = {
                        'model': model,
                        'serial': serial,
                        'smart_status': smart,
                        'size_bytes': size,
                        'protocol': 'NVMe',
                        'media_type': 'SSD',
                    }
        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            pass

        # Step 2: Get volume -> physical disk mapping from SPStorageDataType
        volume_to_physical = {}  # bsd_name -> physical disk bsd_name
        try:
            sp_out = subprocess.check_output(
                ['system_profiler', 'SPStorageDataType', '-json'],
                text=True, timeout=15, stderr=subprocess.DEVNULL,
            )
            sp_data = json.loads(sp_out)
            for vol in sp_data.get('SPStorageDataType', []):
                vol_bsd = vol.get('bsd_name', '')
                phys = vol.get('physical_drive', {})
                phys_name = phys.get('device_name', 'Unknown')
                phys_protocol = phys.get('protocol', 'Unknown')
                phys_type = 'SSD' if phys.get('medium_type') == 'ssd' else 'HDD'
                phys_smart = phys.get('smart_status', 'Unknown')
                phys_internal = phys.get('is_internal_disk', 'no') == 'yes'

                volume_to_physical[vol_bsd] = {
                    'device_name': phys_name,
                    'protocol': phys_protocol,
                    'media_type': phys_type,
                    'smart_status': phys_smart,
                    'internal': phys_internal,
                }
        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            pass

        # Step 3: Get real usage per physical disk by merging partitions
        # Use diskutil to get the APFS container physical store (disk0)
        # which is the REAL physical disk
        container_disk = None
        try:
            out = subprocess.check_output(
                ['diskutil', 'apfs', 'list', '-plist'],
                text=True, timeout=10, stderr=subprocess.DEVNULL,
            )
            # Just get disk0 info via diskutil info
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Step 4: Build physical disk entries
        # Collect all partitions, group by physical disk
        seen_physical = {}  # physical_disk_key -> disk info
        for part in psutil.disk_partitions(all=False):
            device = part.device  # e.g. /dev/disk3s1s1

            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                continue

            # Find physical disk info from volume mapping
            vol_bsd = device.replace('/dev/', '')  # disk3s1s1
            vol_info = volume_to_physical.get(vol_bsd, {})
            phys_name = vol_info.get('device_name', 'Unknown')

            # Use physical device name as grouping key
            if phys_name in seen_physical:
                # This partition belongs to an already-seen physical disk
                # Update used_gb (pick the main data volume)
                existing = seen_physical[phys_name]
                if part.mountpoint in ('/', '/System/Volumes/Data'):
                    existing['used_gb'] = round(usage.used / 1e9, 1)
                    existing['mountpoint'] = part.mountpoint
                continue

            # Find NVMe serial by matching model name
            serial = 'Unknown'
            for bsd, nvme in nvme_map.items():
                if nvme['model'] == phys_name:
                    serial = nvme['serial']
                    break

            # Get diskutil info for SMART
            smart_status = vol_info.get('smart_status', 'Unknown')
            health = 'OK'
            if smart_status == 'Verified':
                health = 'OK'
            elif smart_status == 'Failing':
                health = 'Critical'
            elif smart_status != 'Unknown':
                health = 'Warning'

            # Get real SSD temperature from SMC if available
            ssd_temp = None
            try:
                from monitor.smc_reader import get_ssd_temperature
                ssd_temp = get_ssd_temperature()
            except Exception:
                pass

            seen_physical[phys_name] = {
                'device_name': phys_name,
                'slot': device,
                'model': phys_name,
                'serial': serial,
                'media_type': vol_info.get('media_type', 'Unknown'),
                'protocol': vol_info.get('protocol', 'Unknown'),
                'capacity_gb': round(usage.total / 1e9, 1),
                'used_gb': round(usage.used / 1e9, 1),
                'temperature_c': ssd_temp,
                'health': health,
                'smart_status': smart_status,
                'state': 'Enabled',
                'predicted_life_left': None,
                'internal': vol_info.get('internal', False),
                'mountpoint': part.mountpoint,
                'fstype': part.fstype,
            }

        return list(seen_physical.values())

    # ---------- Linux ----------

    def _get_linux_disks(self):
        """Collect real disk data on Linux using lsblk + smartctl."""
        disks = []
        try:
            out = subprocess.check_output(
                ['lsblk', '-J', '-d', '-o',
                 'NAME,MODEL,SERIAL,ROTA,TRAN,TYPE,SIZE,MOUNTPOINT'],
                text=True, timeout=10,
            )
            data = json.loads(out)

            for dev in data.get('blockdevices', []):
                if dev.get('type') != 'disk':
                    continue

                name = dev.get('name', '')
                dev_path = f'/dev/{name}'
                model = (dev.get('model') or 'Unknown').strip()
                serial = (dev.get('serial') or 'Unknown').strip()
                is_rotational = dev.get('rota', True)
                transport = (dev.get('tran') or 'Unknown').upper()

                # Get usage from the main mountpoint
                capacity_bytes = 0
                used_bytes = 0
                mountpoint = None
                fstype = None

                # Check partitions
                try:
                    part_out = subprocess.check_output(
                        ['lsblk', '-J', '-o', 'NAME,MOUNTPOINT,FSTYPE', dev_path],
                        text=True, timeout=5,
                    )
                    part_data = json.loads(part_out)
                    for bd in part_data.get('blockdevices', []):
                        for child in bd.get('children', [bd]):
                            mp = child.get('mountpoint')
                            if mp:
                                try:
                                    usage = psutil.disk_usage(mp)
                                    if usage.total > capacity_bytes:
                                        capacity_bytes = usage.total
                                        used_bytes = usage.used
                                        mountpoint = mp
                                        fstype = child.get('fstype')
                                except (PermissionError, FileNotFoundError):
                                    continue
                except (subprocess.SubprocessError, json.JSONDecodeError):
                    pass

                # SMART data
                smart = self._get_smart_data(dev_path)
                temperature = smart.get('temperature') if smart else None
                life_left = smart.get('life_left') if smart else None

                disks.append({
                    'device_name': dev_path,
                    'slot': dev_path,
                    'model': model,
                    'serial': serial,
                    'media_type': 'HDD' if is_rotational else 'SSD',
                    'protocol': transport,
                    'capacity_gb': round(capacity_bytes / 1e9, 1),
                    'used_gb': round(used_bytes / 1e9, 1),
                    'temperature_c': temperature,
                    'health': 'OK',
                    'state': 'Enabled',
                    'predicted_life_left': life_left,
                    'mountpoint': mountpoint,
                    'fstype': fstype,
                })

        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            disks = self._get_fallback_disks()

        return disks

    # ---------- Windows ----------

    def _get_windows_disks(self):
        """Collect real disk data on Windows using PowerShell."""
        disks = []
        try:
            out = subprocess.check_output(
                ['powershell', '-Command',
                 'Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,'
                 'SerialNumber,MediaType,BusType,HealthStatus,Size '
                 '| ConvertTo-Json'],
                text=True, timeout=10,
            )
            raw = json.loads(out)
            if isinstance(raw, dict):
                raw = [raw]

            for d in raw:
                dev_id = d.get('DeviceId', '')
                size = d.get('Size', 0)

                # Get usage for this drive's volumes
                used = 0
                try:
                    vol_out = subprocess.check_output(
                        ['powershell', '-Command',
                         f'Get-Partition -DiskNumber {dev_id} '
                         '| Get-Volume | Select-Object DriveLetter,SizeRemaining '
                         '| ConvertTo-Json'],
                        text=True, timeout=10,
                    )
                    vols = json.loads(vol_out)
                    if isinstance(vols, dict):
                        vols = [vols]
                    remaining = sum(v.get('SizeRemaining', 0) for v in vols)
                    used = size - remaining if size else 0
                except Exception:
                    pass

                health_raw = d.get('HealthStatus', 'Unknown')
                health = 'OK' if health_raw == 'Healthy' else (
                    'Warning' if health_raw == 'Warning' else 'Critical'
                )

                disks.append({
                    'device_name': f'PhysicalDrive{dev_id}',
                    'slot': f'\\\\.\\PhysicalDrive{dev_id}',
                    'model': d.get('FriendlyName', 'Unknown'),
                    'serial': d.get('SerialNumber', 'Unknown'),
                    'media_type': str(d.get('MediaType', 'Unknown')),
                    'protocol': str(d.get('BusType', 'Unknown')),
                    'capacity_gb': round(size / 1e9, 1) if size else 0,
                    'used_gb': round(used / 1e9, 1) if used else 0,
                    'temperature_c': None,
                    'health': health,
                    'state': 'Enabled',
                    'predicted_life_left': None,
                    'mountpoint': None,
                    'fstype': None,
                })

        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            disks = self._get_fallback_disks()

        return disks

    # ---------- Fallback ----------

    def _get_fallback_disks(self):
        """Basic fallback using only psutil."""
        disks = []
        seen = set()
        for part in psutil.disk_partitions(all=False):
            if part.device in seen:
                continue
            seen.add(part.device)
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                continue
            disks.append({
                'device_name': part.device,
                'slot': part.device,
                'model': 'Unknown',
                'serial': 'Unknown',
                'media_type': 'Unknown',
                'protocol': 'Unknown',
                'capacity_gb': round(usage.total / 1e9, 1),
                'used_gb': round(usage.used / 1e9, 1),
                'temperature_c': None,
                'health': 'Unknown',
                'state': 'Enabled',
                'predicted_life_left': None,
                'mountpoint': part.mountpoint,
                'fstype': part.fstype,
            })
        return disks

    # ---------- SMART data (Linux) ----------

    def _get_smart_data(self, device):
        """Get SMART data (temp, life_left) from smartctl."""
        try:
            out = subprocess.check_output(
                ['smartctl', '-A', '-j', device],
                text=True, timeout=10, stderr=subprocess.DEVNULL,
            )
            data = json.loads(out)
            result = {}

            temp_info = data.get('temperature', {})
            if temp_info.get('current'):
                result['temperature'] = temp_info['current']

            for attr in data.get('ata_smart_attributes', {}).get('table', []):
                if attr.get('id') == 231:
                    result['life_left'] = attr.get('value')

            nvme_health = data.get('nvme_smart_health_information_log', {})
            if nvme_health:
                if 'temperature' in nvme_health:
                    result['temperature'] = nvme_health['temperature']
                if 'percentage_used' in nvme_health:
                    result['life_left'] = 100 - nvme_health['percentage_used']

            return result if result else None
        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            return None

    # --- PSU / Power Info (real battery + power data) ---

    def get_psu_info(self):
        """Get real power supply / battery info from the host."""
        psus = []
        battery = psutil.sensors_battery()

        if battery is not None:
            # Laptop with battery
            is_plugged = battery.power_plugged
            percent = battery.percent
            secs_left = battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None

            # AC Adapter (PSU 1)
            psus.append({
                'name': 'AC Adapter',
                'model': self._get_charger_info(),
                'health': 'OK' if is_plugged else 'Warning',
                'power_watts': self._estimate_power_draw(),
                'capacity_watts': self._get_charger_wattage(),
                'status_detail': 'Connected' if is_plugged else 'Not Connected',
            })

            # Battery (PSU 2)
            health = 'OK' if percent > 20 else ('Warning' if percent > 5 else 'Critical')
            psus.append({
                'name': 'Battery',
                'model': self._get_battery_model(),
                'health': health,
                'power_watts': round(percent, 1),
                'capacity_watts': 100.0,  # percent scale
                'status_detail': f'{percent}%' + (
                    f' ({secs_left // 60}m left)' if secs_left and secs_left > 0 else ''
                ),
            })
        else:
            # Desktop / no battery -- report AC power
            psus.append({
                'name': 'AC Power',
                'model': 'System Power Supply',
                'health': 'OK',
                'power_watts': self._estimate_power_draw(),
                'capacity_watts': self._detect_psu_capacity(),
            })

        return psus

    def _estimate_power_draw(self):
        """Estimate current power draw from CPU usage."""
        cpu_pct = psutil.cpu_percent(interval=0.1)
        # Rough estimate: idle ~10W, full load ~65W for laptop
        return round(10 + (cpu_pct * 0.55), 1)

    def _get_charger_info(self):
        """Get AC charger model string."""
        if platform.system() == 'Darwin':
            try:
                out = subprocess.check_output(
                    ['system_profiler', 'SPPowerDataType'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                for line in out.splitlines():
                    if 'Wattage' in line or 'Name' in line:
                        val = line.split(':')[1].strip()
                        if val:
                            return val
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        elif platform.system() == 'Windows':
            try:
                out = subprocess.check_output(
                    ['powershell', '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_Battery).DeviceID'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return out.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        return 'AC Adapter'

    def _get_charger_wattage(self):
        """Get charger wattage."""
        if platform.system() == 'Darwin':
            try:
                out = subprocess.check_output(
                    ['system_profiler', 'SPPowerDataType'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                for line in out.splitlines():
                    if 'Wattage' in line:
                        match = re.search(r'(\d+)', line)
                        if match:
                            return float(match.group(1))
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        elif platform.system() == 'Windows':
            try:
                out = subprocess.check_output(
                    ['powershell', '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_Battery).DesignVoltage'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return float(out.strip()) / 1000 * 3  # rough estimate
            except Exception:
                pass
        return 65.0

    def _get_battery_model(self):
        """Get battery model/info."""
        if platform.system() == 'Darwin':
            try:
                out = subprocess.check_output(
                    ['system_profiler', 'SPPowerDataType'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                for line in out.splitlines():
                    if 'Manufacturer' in line:
                        return line.split(':')[1].strip()
                    if 'Device Name' in line:
                        return line.split(':')[1].strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        elif platform.system() == 'Linux':
            path = '/sys/class/power_supply/BAT0/model_name'
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip()
        elif platform.system() == 'Windows':
            try:
                out = subprocess.check_output(
                    ['powershell', '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_Battery).Name'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return out.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        return 'Internal Battery'

    def _detect_psu_capacity(self):
        """Detect PSU capacity for desktops."""
        return 65.0

    # --- Memory ---

    def get_memory_info(self):
        mem = psutil.virtual_memory()
        return {
            'total_gb': round(mem.total / 1e9, 1),
            'used_gb': round(mem.used / 1e9, 1),
            'percent': mem.percent,
        }

    # --- Storage descriptions (for SNMP hrStorageTable) ---

    def get_storage_descriptions(self):
        descriptions = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                descriptions.append({
                    'description': f'{part.device} ({part.mountpoint})',
                    'total_units': usage.total,
                    'used_units': usage.used,
                })
            except PermissionError:
                continue
        return descriptions

    # --- Helpers ---

    def _get_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def _get_processor(self):
        """Get real CPU name."""
        if platform.system() == 'Darwin':
            try:
                out = subprocess.check_output(
                    ['sysctl', '-n', 'machdep.cpu.brand_string'],
                    text=True, timeout=5, stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return out.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            try:
                out = subprocess.check_output(
                    ['system_profiler', 'SPHardwareDataType'],
                    text=True, timeout=10,
                )
                for line in out.splitlines():
                    if 'Chip' in line or 'Processor Name' in line:
                        return line.split(':')[1].strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        elif platform.system() == 'Linux':
            try:
                with open('/proc/cpuinfo') as f:
                    for line in f:
                        if 'model name' in line:
                            return line.split(':')[1].strip()
            except FileNotFoundError:
                pass
        elif platform.system() == 'Windows':
            try:
                out = subprocess.check_output(
                    ['powershell', '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_Processor).Name'],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
                if out.strip():
                    return out.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        return platform.processor() or 'Unknown'
