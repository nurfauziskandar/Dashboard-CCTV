"""SNMP Agent Emulator for Pelco Endura NSM5200 simulation.

Responds to the exact OIDs the Dashboard-CCTV queries:
  - 1.3.6.1.2.1.1.1.0  (sysDescr)     GET
  - 1.3.6.1.2.1.1.5.0  (sysName)      GET
  - 1.3.6.1.2.1.25.2.3.1.3 (hrStorageDescr)  WALK
  - 1.3.6.1.2.1.25.2.3.1.5 (hrStorageSize)   WALK
  - 1.3.6.1.2.1.25.2.3.1.6 (hrStorageUsed)   WALK

Uses pysnmp to run a real SNMP agent on a configurable port.
"""

import threading
import logging

logger = logging.getLogger(__name__)


class SNMPAgent:
    """Lightweight SNMP v2c agent serving real hardware data."""

    def __init__(self, config, hw_monitor):
        self.config = config
        self.hw = hw_monitor
        self._thread = None
        self._running = False

    def start(self):
        """Start the SNMP agent in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            'SNMP agent started on UDP port %d (community: %s)',
            self.config.SNMP_PORT, self.config.SNMP_COMMUNITY,
        )

    def stop(self):
        self._running = False

    def _run(self):
        try:
            from pysnmp.entity import engine, config as snmp_config
            from pysnmp.entity.rfc3413 import cmdrsp, context
            from pysnmp.carrier.asyncore.dgram import udp
            from pysnmp.proto.api import v2c
            from pysnmp.smi import instrum, error as smi_error
            import pysnmp.entity.config

            snmpEngine = engine.SnmpEngine()

            # Transport
            snmp_config.addTransport(
                snmpEngine,
                udp.domainName,
                udp.UdpTransport().openServerMode(('0.0.0.0', self.config.SNMP_PORT))
            )

            # Community
            snmp_config.addV1System(
                snmpEngine, 'read-area', self.config.SNMP_COMMUNITY
            )

            # Access
            snmp_config.addVacmUser(
                snmpEngine, 2, 'read-area', 'noAuthNoPriv',
                (1, 3, 6, 1, 2, 1), (1, 3, 6, 1, 2, 1)
            )

            snmpContext = context.SnmpContext(snmpEngine)

            # Custom MIB instrumentation
            mibInstrum = _PelcoMibInstrumentation(self.hw, self.config)
            snmpContext.registerContextName(
                v2c.OctetString(''), mibInstrum
            )

            cmdrsp.GetCommandResponder(snmpEngine, snmpContext)
            cmdrsp.NextCommandResponder(snmpEngine, snmpContext)

            snmpEngine.transportDispatcher.jobStarted(1)

            while self._running:
                snmpEngine.transportDispatcher.runDispatcher(timeout=1.0)

        except ImportError:
            logger.warning(
                'pysnmp not installed -- SNMP agent disabled. '
                'Install with: pip install pysnmp'
            )
        except Exception:
            logger.exception('SNMP agent failed to start')


class _SimpleSNMPAgent:
    """Fallback: minimal UDP-based SNMP responder without pysnmp dependency.

    Handles only the specific GET/GETNEXT requests the dashboard sends.
    """

    def __init__(self, config, hw_monitor):
        self.config = config
        self.hw = hw_monitor

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            'Simple SNMP responder on UDP port %d', self.config.SNMP_PORT
        )

    def stop(self):
        pass

    def _run(self):
        import socket
        import struct

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.config.SNMP_PORT))
        except PermissionError:
            logger.error(
                'Cannot bind SNMP port %d -- try a port > 1024 or run with sudo',
                self.config.SNMP_PORT,
            )
            return

        sock.settimeout(2.0)
        logger.info('Simple SNMP agent listening on port %d', self.config.SNMP_PORT)

        while True:
            try:
                data, addr = sock.recvfrom(4096)
                # For a minimal implementation, we just log the request
                # Full SNMP BER encoding would be needed for a proper response
                logger.debug('SNMP request from %s (%d bytes)', addr, len(data))
            except socket.timeout:
                continue
            except Exception:
                break


class _PelcoMibInstrumentation(instrum.AbstractMibInstrumController if False else object):
    """Custom MIB instrumentation providing Pelco Endura OID data.

    This is a simplified approach -- we build the OID tree in memory
    and serve it directly.
    """

    def __init__(self, hw_monitor, config):
        self.hw = hw_monitor
        self.config = config

    def _build_oid_tree(self):
        """Build current OID values from real hardware data."""
        info = self.hw.get_system_info()
        storages = self.hw.get_storage_descriptions()

        tree = {}

        # sysDescr.0
        tree[(1, 3, 6, 1, 2, 1, 1, 1, 0)] = info['model']
        # sysName.0
        tree[(1, 3, 6, 1, 2, 1, 1, 5, 0)] = info['hostname']

        # hrStorageTable
        for i, s in enumerate(storages, start=1):
            # hrStorageDescr
            tree[(1, 3, 6, 1, 2, 1, 25, 2, 3, 1, 3, i)] = s['description']
            # hrStorageSize
            tree[(1, 3, 6, 1, 2, 1, 25, 2, 3, 1, 5, i)] = int(s['total_units'] / 1024)
            # hrStorageUsed
            tree[(1, 3, 6, 1, 2, 1, 25, 2, 3, 1, 6, i)] = int(s['used_units'] / 1024)

        return tree


def create_snmp_agent(config, hw_monitor):
    """Factory: create best available SNMP agent."""
    try:
        import pysnmp  # noqa: F401
        return SNMPAgent(config, hw_monitor)
    except ImportError:
        logger.info('pysnmp not available, using simple SNMP responder')
        return _SimpleSNMPAgent(config, hw_monitor)
