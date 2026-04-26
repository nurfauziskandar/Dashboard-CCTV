"""Pelco Server Storage Simulator.

Runs three services:
  1. Management Web UI       (HTTP, default port 8080)
  2. iDRAC Redfish Emulator  (HTTPS, default port 8443)
  3. SNMP Agent              (UDP, default port 10161)
  4. RTSP Recorder           (captures camera streams to disk)

The Dashboard-CCTV can connect to this simulator just like a real
Pelco VX Storage or Endura NSM5200 server.

Usage:
  python3 run.py                              # Default VX Storage mode
  SERVER_TYPE=endura python3 run.py           # Endura NSM5200 mode
  REDFISH_PORT=443 sudo python3 run.py        # Real port 443 (needs root)
"""

import os
import ssl
import logging
import threading
from datetime import timedelta

from flask import Flask
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('server-storage')


def create_app(config=None):
    """Create the Flask application with all services."""
    if config is None:
        config = Config()

    app = Flask(__name__,
                template_folder='web/templates',
                static_folder='web/static')
    app.secret_key = config.SECRET_KEY
    app.config['APP_CONFIG'] = config

    # Session security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

    # Ensure recordings directory exists
    os.makedirs(config.RECORDINGS_DIR, exist_ok=True)

    # Init hardware monitor
    from monitor.hardware import HardwareMonitor
    hw_monitor = HardwareMonitor(config)
    app.config['hw_monitor'] = hw_monitor

    # Init recording manager
    from recorder.stream_recorder import RecordingManager
    rec_manager = RecordingManager(config)
    app.config['rec_manager'] = rec_manager

    # Register blueprints
    from web.routes import bp as web_bp
    from web.auth import bp as auth_bp
    from web.api import bp as api_bp
    from emulator.redfish import bp as redfish_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(redfish_bp)

    return app, config, hw_monitor, rec_manager


def generate_self_signed_cert(cert_dir):
    """Generate a self-signed SSL cert for the Redfish HTTPS server."""
    cert_path = os.path.join(cert_dir, 'cert.pem')
    key_path = os.path.join(cert_dir, 'key.pem')

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime, timedelta, timezone

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'iDRAC-Simulator'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Pelco VxStorage Sim'),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    os.makedirs(cert_dir, exist_ok=True)

    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    logger.info('Generated self-signed SSL cert at %s', cert_path)
    return cert_path, key_path


def run_redfish_server(app, config):
    """Run the Redfish API on HTTPS in a separate thread."""
    cert_dir = os.path.join(os.path.dirname(__file__), '.certs')
    cert_path, key_path = generate_self_signed_cert(cert_dir)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert_path, key_path)

    logger.info(
        'Redfish API (iDRAC emulator) starting on https://0.0.0.0:%d',
        config.REDFISH_PORT,
    )
    logger.info(
        '  Auth: %s / %s', config.REDFISH_USERNAME, config.REDFISH_PASSWORD,
    )

    app.run(
        host='0.0.0.0',
        port=config.REDFISH_PORT,
        ssl_context=ssl_ctx,
        threaded=True,
        use_reloader=False,
    )


def run_web_server(app, config):
    """Run the management web UI on HTTP."""
    logger.info(
        'Management UI starting on http://0.0.0.0:%d', config.WEB_PORT,
    )
    app.run(
        host='0.0.0.0',
        port=config.WEB_PORT,
        threaded=True,
        use_reloader=False,
    )


def main():
    app, config, hw_monitor, rec_manager = create_app()

    # Print startup banner
    ip = hw_monitor.get_system_info()['ip_address']
    print()
    print('=' * 60)
    print('  Pelco Server Storage Simulator')
    print('=' * 60)
    print(f'  Server Type : {config.SERVER_TYPE.upper()}')
    print(f'  Model       : {config.SYSTEM_MODEL}')
    print(f'  IP Address  : {ip}')
    print()
    print(f'  Management UI     : http://{ip}:{config.WEB_PORT}')
    print(f'  Redfish API       : https://{ip}:{config.REDFISH_PORT}')
    print(f'  SNMP Agent        : udp://{ip}:{config.SNMP_PORT}')
    print(f'  Recordings Dir    : {config.RECORDINGS_DIR}')
    print()
    print('  Dashboard-CCTV config:')
    if config.SERVER_TYPE == 'vxstorage':
        print(f'    Server Type     : Pelco VX Storage')
        print(f'    iDRAC IP        : {ip}:{config.REDFISH_PORT}')
        print(f'    iDRAC Username  : {config.REDFISH_USERNAME}')
        print(f'    iDRAC Password  : {config.REDFISH_PASSWORD}')
    else:
        print(f'    Server Type     : Pelco Endura (NSM5200)')
        print(f'    IP Address      : {ip}')
        print(f'    SNMP Community  : {config.SNMP_COMMUNITY}')
    print('=' * 60)
    print()

    # Start RTSP recorders
    rec_manager.start()
    logger.info('Recording manager started')

    # Start SNMP agent
    if config.SERVER_TYPE == 'endura':
        from emulator.snmp_agent import create_snmp_agent
        snmp = create_snmp_agent(config, hw_monitor)
        snmp.start()
    else:
        logger.info('SNMP agent skipped (server_type=vxstorage)')

    # Start Redfish API server in background thread
    redfish_thread = threading.Thread(
        target=run_redfish_server, args=(app, config), daemon=True,
    )
    redfish_thread.start()

    # Run web UI on main thread (blocking)
    try:
        run_web_server(app, config)
    except KeyboardInterrupt:
        logger.info('Shutting down...')
        rec_manager.stop()


if __name__ == '__main__':
    main()
