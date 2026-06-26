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

# Axxon Next 4.x REST API paths (in priority order for auto-detection)
_CAMERA_LIST_PATHS = [
    '/api/v1/cameras',
    '/api/cameras',
    '/api/v1/video/cameras',
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
        items = raw if isinstance(raw, list) else raw.get('cameras', raw.get('data', []))

        for cam in items:
            cam_id = (
                cam.get('id') or cam.get('cameraId') or
                cam.get('camera_id') or cam.get('Id', '')
            )
            name = (
                cam.get('displayName') or cam.get('name') or
                cam.get('Name') or cam.get('caption') or cam_id
            )
            state = str(
                cam.get('state') or cam.get('status') or cam.get('State', '')
            ).lower()
            is_active = state in ('active', 'online', 'connected', 'enabled', '')

            rtsp_main = (
                cam.get('rtspUrl') or cam.get('streamUrl') or
                cam.get('mainStream') or
                f'rtsp://{host}:554/live/{cam_id}/main'
            )
            rtsp_sub = (
                cam.get('subStreamUrl') or cam.get('subStream') or
                f'rtsp://{host}:554/live/{cam_id}/sub'
            )

            cameras.append({
                'id': cam_id,
                'name': name,
                'is_active': is_active,
                'state': state,
                'rtsp_main': rtsp_main,
                'rtsp_sub': rtsp_sub,
                'manufacturer': cam.get('manufacturer') or 'Axxon Next',
                'model': cam.get('model') or cam.get('deviceModel'),
                'ip_address': cam.get('ip') or cam.get('address') or host,
                'location': cam.get('location') or cam.get('locationName'),
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
