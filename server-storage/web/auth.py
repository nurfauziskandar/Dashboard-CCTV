"""Session-based login for the Storage management UI.

Plus shared helpers used by web/api routes:
  - login_required (session)
  - api_token_required (bearer / X-API-Token header)
  - sign_url / verify_signature (HMAC-SHA256 signed URLs for MP4 serve)
"""

import hmac
import time
import hashlib
from functools import wraps
from collections import defaultdict, deque
from urllib.parse import quote

from flask import (
    Blueprint, request, session, redirect, url_for, render_template,
    flash, current_app, jsonify, abort,
)
from werkzeug.security import generate_password_hash, check_password_hash


bp = Blueprint('auth', __name__)


# --- Password handling ---

def _password_hash(app):
    """Return the configured admin password hash. Hash plaintext on first use."""
    cfg = app.config['APP_CONFIG']
    if cfg.ADMIN_PASSWORD_HASH:
        return cfg.ADMIN_PASSWORD_HASH
    cached = app.config.get('_ADMIN_PWHASH')
    if not cached:
        cached = generate_password_hash(cfg.ADMIN_PASSWORD)
        app.config['_ADMIN_PWHASH'] = cached
    return cached


# --- Login rate limiting (per-IP, in-memory) ---

_LOGIN_ATTEMPTS = defaultdict(deque)
_RATE_WINDOW = 900  # 15 min
_RATE_MAX = 5


def _rate_limited(ip):
    now = time.time()
    q = _LOGIN_ATTEMPTS[ip]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    return len(q) >= _RATE_MAX


def _record_attempt(ip):
    _LOGIN_ATTEMPTS[ip].append(time.time())


# --- Decorators ---

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapper


def api_token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        cfg = current_app.config['APP_CONFIG']
        token = request.headers.get('X-API-Token') or ''
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:]
        if not token or not hmac.compare_digest(token, cfg.API_TOKEN):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper


# --- Signed URLs ---

def sign_url(path, ttl=None):
    """Return query string `expires=<ts>&sig=<hex>` for a path."""
    cfg = current_app.config['APP_CONFIG']
    expires = int(time.time()) + int(ttl or cfg.SIGNED_URL_TTL_SECONDS)
    msg = f'{path}|{expires}'.encode('utf-8')
    sig = hmac.new(
        cfg.URL_SIGNING_SECRET.encode('utf-8'),
        msg, hashlib.sha256,
    ).hexdigest()
    return f'expires={expires}&sig={sig}'


def verify_signature(path, expires, sig):
    cfg = current_app.config['APP_CONFIG']
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False
    if expires < int(time.time()):
        return False
    msg = f'{path}|{expires}'.encode('utf-8')
    expected = hmac.new(
        cfg.URL_SIGNING_SECRET.encode('utf-8'),
        msg, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig or '')


# --- Routes ---

@bp.route('/login', methods=['GET', 'POST'])
def login():
    cfg = current_app.config['APP_CONFIG']
    next_url = request.args.get('next') or request.form.get('next') or url_for('web.index')
    ip = request.remote_addr or 'unknown'

    if request.method == 'POST':
        if _rate_limited(ip):
            flash('Terlalu banyak percobaan login. Coba lagi 15 menit.', 'danger')
            return render_template('login.html', next=next_url, config=cfg), 429

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        valid = (
            username == cfg.ADMIN_USERNAME
            and check_password_hash(_password_hash(current_app), password)
        )
        if valid:
            session.clear()
            session['user'] = username
            session.permanent = True
            return redirect(next_url)

        _record_attempt(ip)
        flash('Username atau password salah.', 'danger')

    return render_template('login.html', next=next_url, config=cfg)


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
