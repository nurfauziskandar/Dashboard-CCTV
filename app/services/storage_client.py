"""HTTP client for the server-storage REST API.

Handles auto-registration of cameras to storage backend and fetching
recording metadata + signed URLs for playback.

Storage exposes everything keyed by `slug` (filesystem-safe id, no spaces);
display names with spaces stay in the UI. The slug algorithm here MUST
match the one in server-storage/recorder/stream_recorder.py — both sides
compute slug from the display name independently.
"""

import re
import logging
from urllib.parse import quote
import requests

log = logging.getLogger(__name__)


_UNSAFE = re.compile(r'[<>:"/\\|?*%\x00-\x1f]')
_WS = re.compile(r'\s+')
_MULTI_USCORE = re.compile(r'_+')


def slugify(name):
    """Mirror of server-storage's slugify(). Used to build URL paths
    that match the storage server's routing without an extra round-trip."""
    if not name:
        return 'camera'
    s = name.strip()
    s = _WS.sub('_', s)
    s = _UNSAFE.sub('', s)
    s = _MULTI_USCORE.sub('_', s)
    s = s.strip('._-')
    return s or 'camera'


class StorageClient:

    def __init__(self, base_url, api_token, timeout=5):
        self.base_url = (base_url or '').rstrip('/')
        self.api_token = api_token
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.base_url)

    def _headers(self):
        return {
            'X-API-Token': self.api_token,
            'Content-Type': 'application/json',
        }

    # --- Cameras ---

    def register_camera(self, name, rtsp_uri, metadata=None):
        if not self.enabled:
            return False
        body = {'name': name, 'rtsp_uri': rtsp_uri}
        if metadata:
            for k, v in metadata.items():
                if v not in (None, ''):
                    body[k] = v
        try:
            r = requests.post(
                f'{self.base_url}/api/cameras',
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code in (200, 201):
                return True
            log.warning('register_camera failed: %d %s', r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning('register_camera error: %s', e)
        return False

    def unregister_camera(self, name):
        if not self.enabled:
            return False
        slug = slugify(name)
        try:
            r = requests.delete(
                f'{self.base_url}/api/cameras/{quote(slug, safe="")}',
                headers=self._headers(),
                timeout=self.timeout,
            )
            return r.status_code in (200, 204)
        except requests.RequestException as e:
            log.warning('unregister_camera error: %s', e)
        return False

    def list_registered(self):
        """Return set of camera slugs currently registered on storage."""
        if not self.enabled:
            return set()
        try:
            r = requests.get(
                f'{self.base_url}/api/cameras',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return {c['slug'] for c in r.json().get('cameras', []) if c.get('slug')}
        except requests.RequestException as e:
            log.warning('list_registered error: %s', e)
        return set()

    def list_cameras(self):
        """Return full camera list from storage (includes slug, name,
        rtsp_uri). Used by dashboard's discover endpoint to suck in
        cameras already registered with the storage server."""
        if not self.enabled:
            return []
        try:
            r = requests.get(
                f'{self.base_url}/api/cameras',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json().get('cameras', [])
        except requests.RequestException as e:
            log.warning('list_cameras error: %s', e)
        return []

    # --- Recordings ---

    def list_recordings(self, name):
        """Return list of {name, size_mb, modified, url} or empty list on error."""
        if not self.enabled:
            return []
        slug = slugify(name)
        try:
            r = requests.get(
                f'{self.base_url}/api/recordings/{quote(slug, safe="")}',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                files = r.json().get('files', [])
                for f in files:
                    if f.get('url', '').startswith('/'):
                        f['url'] = f'{self.base_url}{f["url"]}'
                return files
            log.warning('list_recordings failed: %d', r.status_code)
        except requests.RequestException as e:
            log.warning('list_recordings error: %s', e)
        return []

    def live_url(self, name):
        """Return absolute signed MJPEG URL for a camera, or None on failure."""
        if not self.enabled:
            return None
        slug = slugify(name)
        try:
            r = requests.get(
                f'{self.base_url}/api/live_url/{quote(slug, safe="")}',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                rel = r.json().get('url', '')
                if rel.startswith('/'):
                    return f'{self.base_url}{rel}'
                return rel
            log.warning('live_url failed: %d', r.status_code)
        except requests.RequestException as e:
            log.warning('live_url error: %s', e)
        return None

    def health(self):
        if not self.enabled:
            return False
        try:
            r = requests.get(f'{self.base_url}/api/health', timeout=self.timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False
