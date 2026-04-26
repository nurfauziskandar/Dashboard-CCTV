"""REST API for Dashboard ↔ Storage integration.

All endpoints require X-API-Token / Bearer auth.
MP4 serve uses signed URLs (expires + sig) — no token needed there.
"""

import os
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, abort, send_file

from web.auth import api_token_required, sign_url, verify_signature


bp = Blueprint('api', __name__, url_prefix='/api')


@bp.route('/cameras', methods=['GET'])
@api_token_required
def list_cameras():
    rec_manager = current_app.config['rec_manager']
    return jsonify({'cameras': rec_manager.get_camera_list()})


@bp.route('/cameras', methods=['POST'])
@api_token_required
def register_camera():
    """Auto-register from dashboard. Body: {name, rtsp_uri}."""
    rec_manager = current_app.config['rec_manager']
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    rtsp_uri = (data.get('rtsp_uri') or '').strip()

    if not name or not rtsp_uri:
        return jsonify({'error': 'name and rtsp_uri required'}), 400

    rec_manager.add_camera(name, rtsp_uri)
    return jsonify({'status': 'ok', 'name': name}), 201


@bp.route('/cameras/<path:name>', methods=['DELETE'])
@api_token_required
def unregister_camera(name):
    rec_manager = current_app.config['rec_manager']
    rec_manager.remove_camera(name)
    return jsonify({'status': 'ok'})


@bp.route('/recordings/<path:camera_name>', methods=['GET'])
@api_token_required
def list_recordings(camera_name):
    """Return list of recordings + signed URL per file."""
    cfg = current_app.config['APP_CONFIG']
    cam_dir = os.path.join(cfg.RECORDINGS_DIR, camera_name)
    if not os.path.isdir(cam_dir):
        return jsonify({'camera': camera_name, 'files': []})

    files = []
    for f in sorted(os.listdir(cam_dir), reverse=True):
        fpath = os.path.join(cam_dir, f)
        if os.path.isfile(fpath) and f.endswith('.mp4'):
            stat = os.stat(fpath)
            path = f'/api/recordings/{camera_name}/{f}'
            qs = sign_url(path)
            files.append({
                'name': f,
                'size_mb': round(stat.st_size / 1e6, 1),
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'url': f'{path}?{qs}',
            })

    return jsonify({'camera': camera_name, 'files': files})


@bp.route('/recordings/<path:camera_name>/<filename>', methods=['GET'])
def serve_recording(camera_name, filename):
    """Public endpoint guarded by signed URL (expires + sig query params)."""
    expires = request.args.get('expires')
    sig = request.args.get('sig')
    path = f'/api/recordings/{camera_name}/{filename}'
    if not verify_signature(path, expires, sig):
        return jsonify({'error': 'Invalid or expired signature'}), 403

    cfg = current_app.config['APP_CONFIG']
    cam_dir = os.path.realpath(os.path.join(cfg.RECORDINGS_DIR, camera_name))
    rec_dir = os.path.realpath(cfg.RECORDINGS_DIR)
    if not cam_dir.startswith(rec_dir):
        abort(403)

    filepath = os.path.join(cam_dir, filename)
    if not os.path.isfile(filepath):
        abort(404)
    return send_file(filepath, mimetype='video/mp4', conditional=True)


@bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})
