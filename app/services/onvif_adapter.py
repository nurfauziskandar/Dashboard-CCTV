from datetime import datetime, timezone


class ONVIFAdapter:
    """Real ONVIF integration for Pelco cameras.
    Requires: pip install onvif-zeep WSDiscovery
    """

    def probe(self, ip, port, username, password):
        try:
            from onvif import ONVIFCamera
            cam = ONVIFCamera(ip, port, username, password)
            info = cam.devicemgmt.GetDeviceInformation()

            media = cam.create_media_service()
            profiles = media.GetProfiles()
            stream_uri = None
            snapshot_uri = None

            if profiles:
                profile = profiles[0]
                stream_setup = {
                    'Stream': 'RTP-Unicast',
                    'Transport': {'Protocol': 'RTSP'}
                }
                stream_resp = media.GetStreamUri({
                    'StreamSetup': stream_setup,
                    'ProfileToken': profile.token,
                })
                stream_uri = stream_resp.Uri

                try:
                    snap_resp = media.GetSnapshotUri({
                        'ProfileToken': profile.token,
                    })
                    snapshot_uri = snap_resp.Uri
                except Exception:
                    snapshot_uri = f'http://{ip}/jpeg'

            return {
                'is_active': True,
                'stream_uri': stream_uri,
                'snapshot_uri': snapshot_uri,
                'model': getattr(info, 'Model', None),
                'firmware': getattr(info, 'FirmwareVersion', None),
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

    def discover(self):
        try:
            from wsdiscovery.discovery import ThreadedWSDiscovery
            wsd = ThreadedWSDiscovery()
            wsd.start()
            services = wsd.searchServices()
            results = []
            for service in services:
                xaddrs = service.getXAddrs()
                if xaddrs:
                    addr = xaddrs[0]
                    ip = addr.split('//')[1].split(':')[0].split('/')[0]
                    results.append({
                        'ip': ip,
                        'port': 80,
                        'name': f'ONVIF Device ({ip})',
                    })
            wsd.stop()
            return results
        except Exception:
            return []

    def get_snapshot(self, ip, port, username, password):
        try:
            import urllib.request
            url = f'http://{ip}:{port}/jpeg'
            auth_handler = urllib.request.HTTPBasicAuthHandler()
            auth_handler.add_password('', url, username, password)
            opener = urllib.request.build_opener(auth_handler)
            response = opener.open(url, timeout=5)
            return response.read()
        except Exception:
            return None
