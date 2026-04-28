"""HTTP client for the server-storage REST API.

Handles auto-registration of cameras to storage backend and fetching
recording metadata + signed URLs for playback.
"""

import logging
from urllib.parse import quote
import requests

log = logging.getLogger(__name__)


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

    def register_camera(self, name, rtsp_uri):
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f'{self.base_url}/api/cameras',
                json={'name': name, 'rtsp_uri': rtsp_uri},
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
        try:
            r = requests.delete(
                f'{self.base_url}/api/cameras/{quote(name, safe="")}',
                headers=self._headers(),
                timeout=self.timeout,
            )
            return r.status_code in (200, 204)
        except requests.RequestException as e:
            log.warning('unregister_camera error: %s', e)
        return False

    def list_registered(self):
        """Return set of camera names currently registered on storage."""
        if not self.enabled:
            return set()
        try:
            r = requests.get(
                f'{self.base_url}/api/cameras',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return {c['name'] for c in r.json().get('cameras', []) if c.get('name')}
        except requests.RequestException as e:
            log.warning('list_registered error: %s', e)
        return set()

    # --- Recordings ---

    def list_recordings(self, name):
        """Return list of {name, size_mb, modified, url} or empty list on error."""
        if not self.enabled:
            return []
        try:
            r = requests.get(
                f'{self.base_url}/api/recordings/{quote(name, safe="")}',
                headers=self._headers(),
                timeout=self.timeout,
            )
            if r.status_code == 200:
                files = r.json().get('files', [])
                # Resolve signed URLs to absolute URLs
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
        try:
            r = requests.get(
                f'{self.base_url}/api/live_url/{quote(name, safe="")}',
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
