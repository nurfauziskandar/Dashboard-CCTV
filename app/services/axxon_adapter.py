"""Axxon Next REST API adapter.

Tested against Axxon Next 4.x — REST API on port 8116, RTSP on port 554.
Auth: HTTP Basic Auth with Axxon Next operator credentials.
"""

import logging
from datetime import datetime, timezone

import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

_TIMEOUT = 8  # seconds

# Axxon Next REST API paths (priority order, auto-detected at runtime).
# 4.4.x: /asip-api/video-origins/ or /video-origins/
# 4.5.x+: may differ (Bearer/gRPC auth)
_CAMERA_LIST_PATHS = [
    '/asip-api/video-origins/',
    '/video-origins/',
    '/api/v1/cameras',
    '/api/cameras',
]


def _session(username, password):
    s = requests.Session()
    s.auth = HTTPBasicAuth(username, password)
    return s


class AxxonNextAdapter:
    """Import and probe cameras managed by an Axxon Next VMS server."""

    # ── Connection test ──────────────────────────────────────────────────────

    def test_connection(self, host, port, username, password):
        """Return {'ok': bool, 'version': str|None, 'error': str|None}."""
        base = f'http://{host}:{port}'
        s = _session(username, password)

        # Try to hit any known endpoint to verify credentials + reachability
        for path in _CAMERA_LIST_PATHS:
            url = base + path
            try:
                r = s.get(url, timeout=_TIMEOUT)
                if r.status_code == 200:
                    log.info('Axxon test_connection OK via %s', path)
                    return {'ok': True, 'api_path': path, 'error': None}
                if r.status_code == 401:
                    return {'ok': False, 'api_path': None,
                            'error': 'Unauthorized — periksa username/password Axxon Next'}
            except requests.exceptions.ConnectionError:
                return {'ok': False, 'api_path': None,
                        'error': f'Tidak bisa terhubung ke {host}:{port}'}
            except requests.exceptions.Timeout:
                return {'ok': False, 'api_path': None,
                        'error': f'Timeout menghubungi {host}:{port}'}
            except Exception as exc:
                log.debug('Axxon test_connection %s: %s', path, exc)

        return {'ok': False, 'api_path': None,
                'error': f'Endpoint Axxon Next tidak ditemukan di {host}:{port}. '
                         'Pastikan port 8116 benar dan API aktif.'}

    # ── List cameras ─────────────────────────────────────────────────────────

    def list_cameras(self, host, port, username, password):
        """Return list of camera dicts from Axxon Next.

        Each dict: {id, name, state, rtsp_main, rtsp_sub, manufacturer, model}
        """
        base = f'http://{host}:{port}'
        s = _session(username, password)

        raw = None
        used_path = None
        for path in _CAMERA_LIST_PATHS:
            url = base + path
            try:
                r = s.get(url, timeout=_TIMEOUT)
                if r.status_code == 200:
                    raw = r.json()
                    used_path = path
                    break
                log.debug('Axxon list_cameras %s → HTTP %d', path, r.status_code)
            except Exception as exc:
                log.debug('Axxon list_cameras %s: %s', path, exc)

        if raw is None:
            log.warning('Axxon list_cameras: no valid response from %s:%d', host, port)
            return []

        cameras = []
        # /video-origins/ returns a list or dict with items
        if isinstance(raw, dict):
            items = (
                raw.get('origins') or raw.get('cameras') or
                raw.get('data') or raw.get('videoOrigins') or
                list(raw.values())[0] if raw else []
            )
            if not isinstance(items, list):
                items = []
        else:
            items = raw if isinstance(raw, list) else []

        log.debug('Axxon list_cameras: raw type=%s len=%d', type(raw).__name__, len(items))

        for cam in items:
            # /video-origins/ — Axxon Next 4.4.x camera object
            # friendlyNameLong is the canonical camera ID (format: "axxon:vhod_1.Vhod_1")
            friendly_long = (
                cam.get('friendlyNameLong') or cam.get('friendly_name_long') or ''
            )
            friendly_short = (
                cam.get('friendlyName') or cam.get('friendly_name') or
                cam.get('displayName') or cam.get('name') or cam.get('Name', '')
            )
            cam_id = (
                cam.get('id') or cam.get('cameraId') or cam.get('Id') or
                friendly_long or friendly_short
            )
            # Axxon camera URL prefix: "axxon:<friendlyNameLong>"
            axxon_ref = friendly_long if friendly_long else f'axxon:{cam_id}'

            name = friendly_short or cam_id

            state = str(
                cam.get('state') or cam.get('status') or cam.get('State', '')
            ).lower()
            is_active = state in ('active', 'online', 'connected', 'enabled', 'recording', '')

            # RTSP URI format for Axxon Next 4.4.x — best-effort, needs field test
            # Common patterns seen in integrations:
            #   rtsp://host:554/<friendlyNameLong>/main
            #   rtsp://host:554/video/<cam_id>
            rtsp_base = friendly_long.replace('axxon:', '') if friendly_long.startswith('axxon:') else cam_id
            rtsp_main = (
                cam.get('rtspUrl') or cam.get('streamUrl') or cam.get('mainStream') or
                f'rtsp://{host}:554/{rtsp_base}'
            )
            rtsp_sub = (
                cam.get('subStreamUrl') or cam.get('subStream') or
                f'rtsp://{host}:554/{rtsp_base}/sub'
            )

            cameras.append({
                'id': axxon_ref,
                'name': name,
                'is_active': is_active,
                'state': state,
                'rtsp_main': rtsp_main,
                'rtsp_sub': rtsp_sub,
                'manufacturer': cam.get('manufacturer') or 'Axxon Next',
                'model': cam.get('model') or cam.get('deviceModel'),
                'ip_address': cam.get('ip') or cam.get('address') or host,
                'location': cam.get('location') or cam.get('locationName'),
                '_raw': cam,  # keep raw for debugging
            })

        log.info('Axxon list_cameras: %d cameras via %s', len(cameras), used_path)
        return cameras

    # ── probe() — compatible with ONVIFAdapter interface ────────────────────

    def probe(self, ip, port, username, password):
        """Check RTSP reachability for a camera already imported from Axxon Next.

        `ip` is expected to be the Axxon server IP.
        `port` is the RTSP port (554).
        The camera's stream_uri is already set at import time — this just
        validates the connection is alive.
        """
        import socket
        try:
            sock = socket.create_connection((ip, port), timeout=5)
            sock.close()
            return {
                'is_active': True,
                'stream_uri': None,   # keep existing URI from DB
                'snapshot_uri': None,
                'model': None,
                'firmware': None,
                'last_seen': datetime.now(timezone.utc),
            }
        except Exception:
            return {
                'is_active': False,
                'stream_uri': None,
                'snapshot_uri': None,
                'model': None,
                'firmware': None,
                'last_seen': None,
            }

    # ── discover() — lists cameras as discoverable devices ──────────────────

    def discover(self):
        """Not applicable for Axxon Next (server-managed). Return empty."""
        return {
            'devices': [],
            'error': 'Gunakan fitur Import Axxon Next untuk menambah kamera dari VMS.',
        }
