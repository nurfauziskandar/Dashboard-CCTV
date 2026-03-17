"""Read real temperatures from Apple SMC (System Management Controller).

Works on both Intel and Apple Silicon Macs via IOKit ctypes calls.
No root/sudo required. No external dependencies.

Data values are native (little-endian on both Intel and ARM).
Key/type identifiers use big-endian (SMC protocol convention).
"""

import struct
import ctypes
import ctypes.util
import logging
import platform

logger = logging.getLogger(__name__)

# Only available on macOS
_AVAILABLE = platform.system() == 'Darwin'

if _AVAILABLE:
    try:
        _iokit = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/IOKit.framework/IOKit'
        )
        _libc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('c'))

        # Declare function signatures to prevent segfaults
        _iokit.IOServiceMatching.restype = ctypes.c_void_p
        _iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        _iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
        _iokit.IOServiceGetMatchingService.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p
        ]
        _iokit.IOServiceOpen.restype = ctypes.c_int32
        _iokit.IOServiceOpen.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _iokit.IOServiceClose.restype = ctypes.c_int32
        _iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        _iokit.IOObjectRelease.restype = ctypes.c_int32
        _iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        _iokit.IOConnectCallStructMethod.restype = ctypes.c_int32
        _libc.mach_task_self.restype = ctypes.c_uint32
    except OSError:
        _AVAILABLE = False

# SMC constants
KERNEL_INDEX_SMC = 2
SMC_CMD_READ_KEYINFO = 9
SMC_CMD_READ_BYTES = 5


# --- Kernel-matching struct layout ---

class _SMCKeyInfoData(ctypes.Structure):
    _fields_ = [
        ('dataSize', ctypes.c_uint32),
        ('dataType', ctypes.c_uint32),
        ('dataAttributes', ctypes.c_uint8),
    ]


class _SMCVersion(ctypes.Structure):
    _fields_ = [
        ('major', ctypes.c_uint8),
        ('minor', ctypes.c_uint8),
        ('build', ctypes.c_uint8),
        ('reserved', ctypes.c_uint8),
        ('release', ctypes.c_uint16),
    ]


class _SMCPLimitData(ctypes.Structure):
    _fields_ = [
        ('version', ctypes.c_uint16),
        ('length', ctypes.c_uint16),
        ('cpuPLimit', ctypes.c_uint32),
        ('gpuPLimit', ctypes.c_uint32),
        ('memPLimit', ctypes.c_uint32),
    ]


class _SMCKeyData(ctypes.Structure):
    _fields_ = [
        ('key', ctypes.c_uint32),
        ('vers', _SMCVersion),
        ('pLimitData', _SMCPLimitData),
        ('keyInfo', _SMCKeyInfoData),
        ('result', ctypes.c_uint8),
        ('status', ctypes.c_uint8),
        ('data8', ctypes.c_uint8),
        ('data32', ctypes.c_uint32),
        ('bytes', ctypes.c_uint8 * 32),
    ]


# Temperature keys per category
TEMP_KEYS = {
    # Apple Silicon CPU
    'Tp01': 'CPU Performance Core 1',
    'Tp05': 'CPU Performance Core 2',
    'Tp09': 'CPU Efficiency Core 1',
    'Tp0D': 'CPU Efficiency Core 2',
    'Tp0T': 'CPU Efficiency Core 3',
    'Tp0X': 'CPU Performance Core 3',
    'Tp0b': 'CPU Performance Core 4',
    'Tp0H': 'CPU Performance Core 5',
    'Tp0L': 'CPU Performance Core 6',
    'Tp0P': 'CPU Performance Core 7',
    # Intel CPU
    'TC0P': 'CPU Proximity',
    'TC0D': 'CPU Die',
    'TC0E': 'CPU VRM',
    # GPU
    'TG0P': 'GPU Proximity',
    'TG0D': 'GPU Die',
    'Tg05': 'GPU Core 1',
    'Tg0D': 'GPU Core 2',
    # NVMe / SSD
    'TH0A': 'NVMe Drive A',
    'TH0B': 'NVMe Drive B',
    'TH0a': 'NVMe Drive A (alt)',
    'Ts0S': 'SSD Controller',
    'Ts0P': 'SSD Proximity',
    # Battery
    'TB0T': 'Battery Sensor 1',
    'TB1T': 'Battery Sensor 2',
    'TB2T': 'Battery Sensor 3',
    # Other
    'TA0P': 'Ambient',
    'TaLP': 'Airflow Left',
    'TaRP': 'Airflow Right',
    'TW0P': 'WiFi Module',
    'Tm0P': 'Memory Proximity',
    'TPCD': 'Platform Controller',
}


def _smc_call(conn, inp, out):
    in_sz = ctypes.c_size_t(ctypes.sizeof(_SMCKeyData))
    out_sz = ctypes.c_size_t(ctypes.sizeof(_SMCKeyData))
    return _iokit.IOConnectCallStructMethod(
        conn, ctypes.c_uint32(KERNEL_INDEX_SMC),
        ctypes.byref(inp), in_sz,
        ctypes.byref(out), ctypes.byref(out_sz),
    )


def _read_smc_key(conn, key_str):
    """Read one SMC key and return decoded float value."""
    key_int = struct.unpack('>I', key_str.encode('ascii')[:4])[0]

    # Get key info (data type + size)
    inp, out = _SMCKeyData(), _SMCKeyData()
    inp.key = key_int
    inp.data8 = SMC_CMD_READ_KEYINFO
    if _smc_call(conn, inp, out) != 0:
        return None

    data_type = out.keyInfo.dataType
    data_size = out.keyInfo.dataSize
    if data_size == 0:
        return None

    # Read value bytes
    inp2, out2 = _SMCKeyData(), _SMCKeyData()
    inp2.key = key_int
    inp2.keyInfo.dataSize = data_size
    inp2.data8 = SMC_CMD_READ_BYTES
    if _smc_call(conn, inp2, out2) != 0:
        return None

    raw = bytes(out2.bytes[:data_size])
    dt = struct.pack('>I', data_type)  # type ID is big-endian

    return _decode_temp(dt, raw)


def _decode_temp(dt, raw):
    """Decode SMC value bytes to temperature float.

    SMC data values are in NATIVE byte order (little-endian on
    both Intel x86 and Apple Silicon ARM).
    """
    # 32-bit float (most common on Apple Silicon)
    if dt == b'flt ' and len(raw) >= 4:
        val = struct.unpack('<f', raw[:4])[0]  # little-endian!
        return round(val, 1) if 0 < val < 150 else None

    # Signed fixed-point (common on Intel Macs)
    if dt == b'sp78' and len(raw) >= 2:
        val = struct.unpack('<h', raw[:2])[0] / 256.0
        return round(val, 1) if 0 < val < 150 else None
    if dt == b'sp87' and len(raw) >= 2:
        val = struct.unpack('<h', raw[:2])[0] / 128.0
        return round(val, 1) if 0 < val < 150 else None

    # IOFloat (2-byte)
    if dt == b'ioft' and len(raw) >= 2:
        val = struct.unpack('<H', raw[:2])[0] / 256.0
        return round(val, 1) if 0 < val < 150 else None

    # Unsigned int types
    if dt == b'ui8 ' and len(raw) >= 1:
        val = float(raw[0])
        return val if 0 < val < 150 else None
    if dt == b'ui16' and len(raw) >= 2:
        val = float(struct.unpack('<H', raw[:2])[0])
        return round(val, 1) if 0 < val < 150 else None

    return None


# --- Public API ---

def read_temperatures():
    """Read all available temperature sensors from Apple SMC.

    Returns dict: {sensor_label: temperature_celsius}
    """
    if not _AVAILABLE:
        return {}

    results = {}
    try:
        matching = _iokit.IOServiceMatching(b'AppleSMC')
        service = _iokit.IOServiceGetMatchingService(0, matching)
        if not service:
            return {}

        conn = ctypes.c_uint32()
        if _iokit.IOServiceOpen(
            service, _libc.mach_task_self(), 0, ctypes.byref(conn)
        ) != 0:
            _iokit.IOObjectRelease(service)
            return {}
        _iokit.IOObjectRelease(service)

        try:
            for key, label in TEMP_KEYS.items():
                val = _read_smc_key(conn, key)
                if val is not None:
                    results[label] = val
        finally:
            _iokit.IOServiceClose(conn)

    except Exception as e:
        logger.debug('SMC read error: %s', e)

    return results


def get_cpu_temperature():
    """Get primary CPU temperature (average of available cores)."""
    temps = read_temperatures()
    cpu_temps = [v for k, v in temps.items() if 'CPU' in k]
    if cpu_temps:
        return round(sum(cpu_temps) / len(cpu_temps), 1)
    if temps:
        return list(temps.values())[0]
    return None


def get_gpu_temperature():
    """Get GPU temperature if available."""
    temps = read_temperatures()
    for name, val in temps.items():
        if 'GPU' in name:
            return val
    return None


def get_ssd_temperature():
    """Get SSD/NVMe temperature if available."""
    temps = read_temperatures()
    for name, val in temps.items():
        if any(k in name for k in ('SSD', 'NVMe', 'Drive')):
            return val
    return None


def get_battery_temperature():
    """Get battery temperature if available."""
    temps = read_temperatures()
    bat_temps = [v for k, v in temps.items() if 'Battery' in k]
    if bat_temps:
        return round(sum(bat_temps) / len(bat_temps), 1)
    return None
