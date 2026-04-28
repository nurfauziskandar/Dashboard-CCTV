"""REST API for Dashboard ↔ Storage integration.

All endpoints require X-API-Token / Bearer auth.
MP4 serve uses signed URLs (expires + sig) — no token needed there.
"""

import os
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, abort, send_file, Response

from web.auth import api_token_required, sign_url, verify_signature
from recorder.stream_recorder import slugify


bp = Blueprint('api', __name__, url_prefix='/api')


@bp.route('/cameras', methods=['GET'])
@api_token_required
def list_cameras():
    """Return list of cameras with full metadata (slug, display name, etc).

    Storage is the source of truth — dashboard discover endpoint pulls
    from here and creates rows that don't exist yet.
    """
    rec_manager = current_app.config['rec_manager']
    return jsonify({'cameras': rec_manager.get_camera_list()})


_META_FIELDS = (
    'ip_address', 'port', 'manufacturer', 'model',
    'location_name', 'latitude', 'longitude',
    'onvif_username', 'onvif_password',
)


@bp.route('/cameras', methods=['POST'])
@api_token_required
def register_camera():
    """Register a camera. Body: {name, rtsp_uri, ...metadata}.

    Backend computes slug from name. All filesystem and URL paths use
    the slug; UI keeps the original name. Optional metadata fields
    (ip_address, port, manufacturer, model, location_name, latitude,
    longitude, onvif_username, onvif_password) get stored as-is so the
    dashboard can pull them on discover.
    """
    rec_manager = current_app.config['rec_manager']
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    rtsp_uri = (data.get('rtsp_uri') or '').strip()

    if not name or not rtsp_uri:
        return jsonify({'error': 'name and rtsp_uri required'}), 400

    metadata = {k: data[k] for k in _META_FIELDS if data.get(k) not in (None, '')}
    slug = rec_manager.add_camera(name, rtsp_uri, metadata=metadata or None)
    return jsonify({'status': 'ok', 'slug': slug, 'name': name}), 201


@bp.route('/cameras/<slug>', methods=['DELETE'])
@api_token_required
def unregister_camera(slug):
    rec_manager = current_app.config['rec_manager']
    rec_manager.remove_camera(slug)
    return jsonify({'status': 'ok'})


@bp.route('/recordings/<slug>', methods=['GET'])
@api_token_required
def list_recordings(slug):
    """Return list of recordings + signed URL per file."""
    cfg = current_app.config['APP_CONFIG']
    cam_dir = os.path.join(cfg.RECORDINGS_DIR, slug)
    if not os.path.isdir(cam_dir):
        return jsonify({'camera': slug, 'files': []})

    files = []
    for f in sorted(os.listdir(cam_dir), reverse=True):
        fpath = os.path.join(cam_dir, f)
        if os.path.isfile(fpath) and f.endswith('.mp4'):
            stat = os.stat(fpath)
            path = f'/api/recordings/{slug}/{f}'
            qs = sign_url(path)
            files.append({
                'name': f,
                'size_mb': round(stat.st_size / 1e6, 1),
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'url': f'{path}?{qs}',
            })

    return jsonify({'camera': slug, 'files': files})


@bp.route('/recordings/<slug>/<filename>', methods=['GET'])
def serve_recording(slug, filename):
    """Public endpoint guarded by signed URL (expires + sig query params)."""
    expires = request.args.get('expires')
    sig = request.args.get('sig')
    path = f'/api/recordings/{slug}/{filename}'
    if not verify_signature(path, expires, sig):
        return jsonify({'error': 'Invalid or expired signature'}), 403

    cfg = current_app.config['APP_CONFIG']
    cam_dir = os.path.realpath(os.path.join(cfg.RECORDINGS_DIR, slug))
    rec_dir = os.path.realpath(cfg.RECORDINGS_DIR)
    if not cam_dir.startswith(rec_dir):
        abort(403)

    filepath = os.path.join(cam_dir, filename)
    if not os.path.isfile(filepath):
        abort(404)
    resp = send_file(filepath, mimetype='video/mp4', conditional=True)
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@bp.route('/live_url/<slug>', methods=['GET'])
@api_token_required
def live_url(slug):
    """Return signed MJPEG live-stream URL for a camera. Uses 8h TTL so the
    browser's <img> auto-retry keeps working over a long viewing session."""
    path = f'/api/live/{slug}'
    qs = sign_url(path, ttl=8 * 3600)
    return jsonify({'camera': slug, 'url': f'{path}?{qs}'})


@bp.route('/live/<slug>', methods=['GET'])
def live_stream(slug):
    """Public endpoint guarded by signed URL. Returns multipart MJPEG."""
    expires = request.args.get('expires')
    sig = request.args.get('sig')
    path = f'/api/live/{slug}'
    if not verify_signature(path, expires, sig):
        return jsonify({'error': 'Invalid or expired signature'}), 403

    live = current_app.config.get('live_service')
    if live is None:
        return jsonify({'error': 'live service unavailable'}), 503

    return Response(
        live.get_frame_generator(slug),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


@bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})
