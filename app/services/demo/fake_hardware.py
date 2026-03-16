import random
from datetime import datetime, timezone

DEMO_SERVERS = [
    {
        'name': 'VxStorage-E-01',
        'ip_address': '192.168.1.10',
        'description': 'Pelco VX Storage E-Series - Gedung A (Primary)',
        'server_type': 'vxstorage',
        'idrac_ip': '192.168.1.110',
        'idrac_username': 'root',
        'idrac_password': 'calvin',
    },
    {
        'name': 'VxStorage-E-02',
        'ip_address': '192.168.1.11',
        'description': 'Pelco VX Storage E-Series - Gedung B',
        'server_type': 'vxstorage',
        'idrac_ip': '192.168.1.111',
        'idrac_username': 'root',
        'idrac_password': 'calvin',
    },
    {
        'name': 'Endura-NSM5200',
        'ip_address': '192.168.1.12',
        'description': 'Pelco Endura NSM5200 - Legacy Storage',
        'server_type': 'endura',
        'snmp_community': 'public',
    },
]

VXSTORAGE_HDD_MODELS = [
    'Seagate SkyHawk AI ST8000VE001 8TB',
    'Seagate SkyHawk AI ST10000VE001 10TB',
    'Seagate Exos X18 ST16000NM000J 16TB',
    'WD Ultrastar DC HC550 WUH721816ALE6L4 16TB',
]

ENDURA_HDD_MODELS = [
    'Seagate Constellation ES.3 ST4000NM0033 4TB',
    'Seagate Constellation ES.3 ST2000NM0033 2TB',
]


class FakeHardwareMonitor:

    def get_server_health(self, server):
        now = datetime.now(timezone.utc)
        if server.server_type == 'endura':
            return {
                'is_online': random.random() > 0.05,
                'system_model': 'Pelco NSM5200-36-US',
                'serial_number': f'NSM-{random.randint(100000, 999999)}',
                'power_state': 'On',
                'health_rollup': 'OK',
                'inlet_temp': None,
                'exhaust_temp': None,
                'cpu_usage': round(random.uniform(20.0, 60.0), 1),
                'memory_usage': round(random.uniform(40.0, 75.0), 1),
                'last_checked': now,
            }
        # VxStorage (Dell PowerEdge via iDRAC)
        return {
            'is_online': random.random() > 0.03,
            'system_model': 'Dell PowerEdge R740xd (Pelco VxStorage E-Series)',
            'serial_number': f'DELL-{random.randint(100000, 999999):06d}',
            'power_state': 'On',
            'health_rollup': random.choice(['OK', 'OK', 'OK', 'OK', 'Warning']),
            'inlet_temp': round(random.uniform(20.0, 35.0), 1),
            'exhaust_temp': round(random.uniform(38.0, 58.0), 1),
            'cpu_usage': round(random.uniform(15.0, 65.0), 1),
            'memory_usage': round(random.uniform(35.0, 80.0), 1),
            'last_checked': now,
        }

    def get_hdds(self, server):
        random.seed(hash(server.ip_address))
        now = datetime.now(timezone.utc)
        hdds = []

        if server.server_type == 'endura':
            num = random.randint(6, 12)
            models = ENDURA_HDD_MODELS
        else:
            num = random.randint(8, 16)
            models = VXSTORAGE_HDD_MODELS

        for i in range(num):
            model = random.choice(models)
            cap_str = model.split()[-1]
            cap_tb = int(cap_str.replace('TB', ''))
            cap_gb = cap_tb * 1000.0
            health = random.choices(
                ['OK', 'OK', 'OK', 'OK', 'Warning', 'Critical'],
                weights=[50, 25, 15, 5, 4, 1],
            )[0]
            random.seed(None)
            hdds.append({
                'device_name': f'Disk {i}' if server.server_type == 'vxstorage' else f'/dev/sd{chr(97 + i)}',
                'slot': f'Slot {i}' if server.server_type == 'vxstorage' else None,
                'model': model.rsplit(' ', 1)[0],
                'serial': f'WD-{random.randint(100000, 999999):06d}',
                'media_type': 'HDD',
                'protocol': 'SAS' if server.server_type == 'vxstorage' else 'SATA',
                'capacity_gb': cap_gb,
                'used_gb': round(random.uniform(cap_gb * 0.3, cap_gb * 0.95), 1),
                'temperature_c': round(random.uniform(30.0, 52.0), 1),
                'health_status': health,
                'state': 'Enabled',
                'predicted_life_left': None,
                'power_on_hours': random.randint(2000, 45000),
                'reallocated_sectors': 0 if health == 'OK' else random.randint(1, 50),
                'pending_sectors': 0 if health != 'Critical' else random.randint(1, 10),
                'last_checked': now,
            })
            random.seed(hash(server.ip_address + str(i + 1)))
        random.seed(None)
        return hdds

    def get_psus(self, server):
        if server.server_type == 'endura':
            return [
                {
                    'name': 'Power Supply 1',
                    'model': 'Pelco NSM PSU-750W',
                    'health_status': 'OK',
                    'power_watts': round(random.uniform(180, 320), 0),
                    'capacity_watts': 750.0,
                },
            ]
        return [
            {
                'name': 'PSU 1',
                'model': 'Dell E2200P-00 2200W',
                'health_status': 'OK',
                'power_watts': round(random.uniform(350, 650), 0),
                'capacity_watts': 2200.0,
            },
            {
                'name': 'PSU 2',
                'model': 'Dell E2200P-00 2200W',
                'health_status': 'OK',
                'power_watts': round(random.uniform(350, 650), 0),
                'capacity_watts': 2200.0,
            },
        ]
