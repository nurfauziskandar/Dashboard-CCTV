import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


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
        # Resolve ThreadedWSDiscovery before doing anything else so an
        # ImportError is clearly a "package not installed" problem, not
        # confused with a network/runtime error later.
        ThreadedWSDiscovery = None
        try:
            from wsdiscovery.discovery import ThreadedWSDiscovery
        except ImportError:
            try:
                from wsdiscovery import ThreadedWSDiscovery
            except ImportError:
                log.error('ONVIF discover failed: WSDiscovery not installed')
                return {
                    'devices': [],
                    'error': (
                        'Package WSDiscovery tidak terinstall. '
                        'Jalankan: pip install WSDiscovery '
                        '(atau aktifkan venv: source venv/bin/activate)'
                    ),
                }

        wsd = None
        try:
            log.info('ONVIF discover: starting WS-Discovery scan')
            wsd = ThreadedWSDiscovery()
            wsd.start()
            services = wsd.searchServices()
            results = []
            seen = set()
            for service in services:
                try:
                    xaddrs = service.getXAddrs()
                except Exception:
                    xaddrs = []
                if not xaddrs:
                    continue
                addr = xaddrs[0]
                try:
                    ip = addr.split('//')[1].split(':')[0].split('/')[0]
                except (IndexError, AttributeError):
                    continue
                if ip in seen:
                    continue
                seen.add(ip)
                results.append({
                    'ip': ip,
                    'port': 80,
                    'name': f'ONVIF Device ({ip})',
                })
            log.info('ONVIF discover: found %d device(s)', len(results))
            return {'devices': results, 'error': None}
        except Exception as exc:
            log.error('ONVIF discover failed: %s: %s', type(exc).__name__, exc, exc_info=True)
            return {'devices': [], 'error': f'{type(exc).__name__}: {exc}'}
        finally:
            if wsd is not None:
                try:
                    wsd.stop()
                except Exception:
                    pass

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
