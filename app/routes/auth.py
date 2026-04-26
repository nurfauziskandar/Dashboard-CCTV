"""Session-based login for the Dashboard."""

import time
from functools import wraps
from collections import defaultdict, deque

from flask import (
    Blueprint, request, session, redirect, url_for, render_template,
    flash, current_app,
)
from werkzeug.security import generate_password_hash, check_password_hash


bp = Blueprint('auth', __name__)


def _password_hash():
    """Return cached admin password hash. Hash plaintext default on first use."""
    cfg = current_app.config
    if cfg.get('ADMIN_PASSWORD_HASH'):
        return cfg['ADMIN_PASSWORD_HASH']
    cached = cfg.get('_ADMIN_PWHASH')
    if not cached:
        cached = generate_password_hash(cfg.get('ADMIN_PASSWORD', 'admin123'))
        cfg['_ADMIN_PWHASH'] = cached
    return cached


# --- Login rate limiting ---

_LOGIN_ATTEMPTS = defaultdict(deque)
_RATE_WINDOW = 900
_RATE_MAX = 5


def _rate_limited(ip):
    now = time.time()
    q = _LOGIN_ATTEMPTS[ip]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    return len(q) >= _RATE_MAX


def _record_attempt(ip):
    _LOGIN_ATTEMPTS[ip].append(time.time())


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapper


@bp.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next') or request.form.get('next') or url_for('dashboard.index')
    ip = request.remote_addr or 'unknown'

    if request.method == 'POST':
        if _rate_limited(ip):
            flash('Terlalu banyak percobaan login. Coba lagi 15 menit.', 'danger')
            return render_template('auth/login.html', next=next_url), 429

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        valid = (
            username == current_app.config.get('ADMIN_USERNAME', 'admin')
            and check_password_hash(_password_hash(), password)
        )
        if valid:
            session.clear()
            session['user'] = username
            session.permanent = True
            return redirect(next_url)

        _record_attempt(ip)
        flash('Username atau password salah.', 'danger')

    return render_template('auth/login.html', next=next_url)


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
