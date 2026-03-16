import random
from datetime import datetime, timezone, timedelta

DEMO_CAMERAS = [
    {
        'name': 'Lobby Utama',
        'ip_address': '192.168.1.101',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IMP Series',
        'firmware': 'V3.2.1',
        'location_name': 'Gedung A - Lobby',
        'latitude': -6.2088,
        'longitude': 106.8456,
    },
    {
        'name': 'Parkir Basement B1',
        'ip_address': '192.168.1.102',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix Enhanced IME',
        'firmware': 'V3.1.0',
        'location_name': 'Gedung A - Basement 1',
        'latitude': -6.2092,
        'longitude': 106.8460,
    },
    {
        'name': 'Koridor Lantai 2',
        'ip_address': '192.168.1.103',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Spectra Pro 2',
        'firmware': 'V5.0.2',
        'location_name': 'Gedung A - Lt. 2',
        'latitude': -6.2085,
        'longitude': 106.8453,
    },
    {
        'name': 'Gerbang Masuk',
        'ip_address': '192.168.1.104',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IBP Series',
        'firmware': 'V3.2.0',
        'location_name': 'Pos Satpam',
        'latitude': -6.2100,
        'longitude': 106.8465,
    },
    {
        'name': 'Ruang Server',
        'ip_address': '192.168.1.105',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IXE Series',
        'firmware': 'V4.0.1',
        'location_name': 'Gedung B - Lt. 1',
        'latitude': -6.2078,
        'longitude': 106.8448,
    },
    {
        'name': 'Gudang Belakang',
        'ip_address': '192.168.1.106',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IBD Series',
        'firmware': 'V3.0.5',
        'location_name': 'Area Gudang',
        'latitude': -6.2095,
        'longitude': 106.8470,
    },
    {
        'name': 'Loading Dock',
        'ip_address': '192.168.1.107',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Spectra Enhanced 7',
        'firmware': 'V6.1.0',
        'location_name': 'Area Loading',
        'latitude': -6.2098,
        'longitude': 106.8475,
    },
    {
        'name': 'Lift Utama Lt.1',
        'ip_address': '192.168.1.108',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IMP Mini Dome',
        'firmware': 'V3.2.1',
        'location_name': 'Gedung A - Lt. 1',
        'latitude': -6.2086,
        'longitude': 106.8455,
    },
    {
        'name': 'Tangga Darurat A',
        'ip_address': '192.168.1.109',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IBP Bullet',
        'firmware': 'V3.1.2',
        'location_name': 'Gedung A - Tangga',
        'latitude': -6.2083,
        'longitude': 106.8450,
    },
    {
        'name': 'Kantin',
        'ip_address': '192.168.1.110',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Sarix IXE Dome',
        'firmware': 'V4.0.0',
        'location_name': 'Gedung B - Lt. Dasar',
        'latitude': -6.2075,
        'longitude': 106.8445,
    },
    {
        'name': 'Gerbang Keluar',
        'ip_address': '192.168.1.111',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Spectra Pro 2',
        'firmware': 'V5.0.2',
        'location_name': 'Pos Satpam Keluar',
        'latitude': -6.2105,
        'longitude': 106.8468,
    },
    {
        'name': 'Rooftop',
        'ip_address': '192.168.1.112',
        'port': 80,
        'manufacturer': 'Pelco',
        'model': 'Spectra Enhanced 7',
        'firmware': 'V6.1.0',
        'location_name': 'Gedung A - Atap',
        'latitude': -6.2087,
        'longitude': 106.8457,
    },
]


class FakeCameraAdapter:

    def probe(self, ip, port, username, password):
        is_active = random.random() > 0.15
        return {
            'is_active': is_active,
            'stream_uri': f'rtsp://{ip}:554/stream1' if is_active else None,
            'snapshot_uri': f'http://{ip}/jpeg' if is_active else None,
            'model': None,
            'firmware': None,
            'last_seen': datetime.now(timezone.utc) if is_active else (
                datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 48))
            ),
        }

    def discover(self):
        count = random.randint(2, 5)
        base = random.randint(200, 240)
        results = []
        for i in range(count):
            results.append({
                'ip': f'192.168.1.{base + i}',
                'port': 80,
                'name': f'Discovered Camera {base + i}',
            })
        return results

    def get_snapshot(self, ip, port, username, password):
        return None
