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

    def health(self):
        if not self.enabled:
            return False
        try:
            r = requests.get(f'{self.base_url}/api/health', timeout=self.timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False
