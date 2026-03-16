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
    DEFAULT_MAP_CENTER = [-6.2088, 106.8456]
    DEFAULT_MAP_ZOOM = 12


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
