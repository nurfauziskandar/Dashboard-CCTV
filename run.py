import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Warn about optional packages missing (won't crash — features just degrade)
_missing = []
for _pkg in ('wsdiscovery', 'onvif'):
    try:
        __import__(_pkg)
    except ImportError:
        _missing.append(_pkg)
if _missing:
    print(
        f'\n[WARNING] Optional package(s) not found: {", ".join(_missing)}\n'
        '  ONVIF camera discovery will be unavailable.\n'
        '  Fix: source venv/bin/activate  (or: pip install WSDiscovery onvif-zeep)\n'
    )

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=app.config['DEBUG'])
