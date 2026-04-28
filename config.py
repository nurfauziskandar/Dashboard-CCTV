import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_PATH = os.path.join(BASE_DIR, 'instance')


class BaseConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(INSTANCE_PATH, 'dashboard.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEMO_MODE = False
    CAMERA_POLL_INTERVAL = 60
    SERVER_POLL_INTERVAL = 120
    FERNET_KEY = os.environ.get('FERNET_KEY')
    DEFAULT_MAP_CENTER = [
        float(os.environ.get('MAP_CENTER_LAT', -6.2088)),
        float(os.environ.get('MAP_CENTER_LNG', 106.8456)),
    ]
    DEFAULT_MAP_ZOOM = int(os.environ.get('MAP_ZOOM', 12))

    # --- Auth (Web UI session login) ---
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
    ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')

    # --- Storage backend (server-storage REST API) ---
    # When set, dashboard auto-registers cameras to this storage and fetches
    # playback recordings from it. Leave blank to disable storage integration.
    STORAGE_URL = os.environ.get('STORAGE_URL', '')
    STORAGE_API_TOKEN = os.environ.get(
        'STORAGE_API_TOKEN',
        'change-me-storage-api-token-min-32-chars-long-please',
    )
    STORAGE_TIMEOUT = int(os.environ.get('STORAGE_TIMEOUT', 5))

    # --- Live Streaming (RTSP -> MJPEG proxy) ---
    # Lower these if dashboard CPU spikes during live view of high-resolution
    # cameras. Browser only sees STREAM_FPS frames/sec, downscaled to
    # STREAM_MAX_WIDTH px wide before JPEG encode.
    STREAM_FPS = int(os.environ.get('STREAM_FPS', 10))
    STREAM_MAX_WIDTH = int(os.environ.get('STREAM_MAX_WIDTH', 1280))
    STREAM_JPEG_QUALITY = int(os.environ.get('STREAM_JPEG_QUALITY', 65))


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    DEMO_MODE = True


class ProductionConfig(BaseConfig):
    DEBUG = False
    DEMO_MODE = False


config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
