import os
import socket
import platform
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _powershell(command):
    """Run a PowerShell command on Windows, return stdout."""
    return subprocess.check_output(
        ['powershell', '-NoProfile', '-Command', command],
        text=True, timeout=10, stderr=subprocess.DEVNULL,
    ).strip()


def _detect_system_model():
    """Detect real hardware model from the host machine."""
    try:
        if platform.system() == 'Darwin':
            out = subprocess.check_output(
                ['system_profiler', 'SPHardwareDataType'],
                text=True, timeout=10,
            )
            for line in out.splitlines():
                if 'Model Name' in line:
                    return line.split(':')[1].strip()
            return subprocess.check_output(
                ['sysctl', '-n', 'hw.model'], text=True, timeout=5,
            ).strip()
        elif platform.system() == 'Linux':
            path = '/sys/class/dmi/id/product_name'
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip()
        elif platform.system() == 'Windows':
            # PowerShell (works on Windows 10/11)
            model = _powershell(
                '(Get-CimInstance Win32_ComputerSystem).Model'
            )
            manufacturer = _powershell(
                '(Get-CimInstance Win32_ComputerSystem).Manufacturer'
            )
            if model:
                return f'{manufacturer} {model}'.strip()
    except Exception:
        pass
    return f'{platform.node()} ({platform.machine()})'


def _detect_serial_number():
    """Detect real hardware serial number."""
    try:
        if platform.system() == 'Darwin':
            out = subprocess.check_output(
                ['system_profiler', 'SPHardwareDataType'],
                text=True, timeout=10,
            )
            for line in out.splitlines():
                if 'Serial Number' in line:
                    return line.split(':')[1].strip()
        elif platform.system() == 'Linux':
            path = '/sys/class/dmi/id/product_serial'
            if os.path.exists(path):
                with open(path) as f:
                    val = f.read().strip()
                    if val and val != 'None':
                        return val
        elif platform.system() == 'Windows':
            serial = _powershell(
                '(Get-CimInstance Win32_BIOS).SerialNumber'
            )
            if serial:
                return serial
    except Exception:
        pass
    return 'Unknown'


class Config:
    """Server Storage Simulator Configuration.

    System identity auto-detected from host hardware.
    Override via environment variables if needed.
    """

    # --- Identity (auto-detected from real hardware) ---
    SERVER_TYPE = os.environ.get('SERVER_TYPE', 'vxstorage')
    SYSTEM_MODEL = os.environ.get('SYSTEM_MODEL', _detect_system_model())
    SERIAL_NUMBER = os.environ.get('SERIAL_NUMBER', _detect_serial_number())
    HOSTNAME = os.environ.get('SERVER_HOSTNAME', socket.gethostname())

    # --- Redfish API (iDRAC emulator) ---
    REDFISH_PORT = int(os.environ.get('REDFISH_PORT', 8443))
    REDFISH_USERNAME = os.environ.get('REDFISH_USERNAME', 'root')
    REDFISH_PASSWORD = os.environ.get('REDFISH_PASSWORD', 'calvin')

    # --- SNMP Agent (Endura emulator) ---
    SNMP_PORT = int(os.environ.get('SNMP_PORT', 10161))
    SNMP_COMMUNITY = os.environ.get('SNMP_COMMUNITY', 'public')

    # --- Management Web UI ---
    WEB_PORT = int(os.environ.get('WEB_PORT', 8080))
    SECRET_KEY = os.environ.get('SECRET_KEY', 'server-storage-sim-dev-key')

    # --- Recording ---
    RECORDINGS_DIR = os.environ.get(
        'RECORDINGS_DIR', os.path.join(BASE_DIR, 'recordings')
    )
    SEGMENT_DURATION = int(os.environ.get('SEGMENT_DURATION', 300))
    VIDEO_CODEC = os.environ.get('VIDEO_CODEC', 'mp4v')
    VIDEO_FPS = int(os.environ.get('VIDEO_FPS', 15))

    # --- Retention Policy ---
    # Recordings older than RETENTION_DAYS will be auto-deleted (0 = disabled)
    RETENTION_DAYS = int(os.environ.get('RETENTION_DAYS', 7))
    # Max total recordings size in GB (oldest files deleted when exceeded, 0 = unlimited)
    MAX_STORAGE_GB = int(os.environ.get('MAX_STORAGE_GB', 50))
    # How often the cleanup job runs (seconds)
    CLEANUP_INTERVAL = int(os.environ.get('CLEANUP_INTERVAL', 300))

    # --- Cameras ---
    CAMERAS_FILE = os.environ.get(
        'CAMERAS_FILE', os.path.join(BASE_DIR, 'cameras.json')
    )
    # Retention settings file (persisted from web UI)
    RETENTION_FILE = os.environ.get(
        'RETENTION_FILE', os.path.join(BASE_DIR, 'retention.json')
    )

    # --- Auth (Web UI session login) ---
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    # Plaintext default password (hashed at runtime). In production set
    # ADMIN_PASSWORD_HASH directly via env to avoid plaintext on disk.
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'storage123')
    ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')

    # --- API Token (Dashboard ↔ Storage) ---
    # Static bearer token. Both dashboard and storage must share the same value.
    API_TOKEN = os.environ.get(
        'STORAGE_API_TOKEN',
        'change-me-storage-api-token-min-32-chars-long-please',
    )

    # --- Signed URL secret (HMAC-SHA256) ---
    URL_SIGNING_SECRET = os.environ.get(
        'URL_SIGNING_SECRET',
        'change-me-url-signing-secret-min-32-chars-long-please',
    )
    SIGNED_URL_TTL_SECONDS = int(os.environ.get('SIGNED_URL_TTL_SECONDS', 300))
