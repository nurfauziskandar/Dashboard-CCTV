"""Management Web UI for the Server Storage Simulator."""

import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file, abort

from web.auth import login_required

bp = Blueprint('web', __name__, url_prefix='/',
               template_folder='templates', static_folder='static')


@bp.route('/')
@login_required
def index():
    hw = current_app.config['hw_monitor']
    rec_manager = current_app.config['rec_manager']
    config = current_app.config['APP_CONFIG']

    import psutil
    system = hw.get_system_info()
    health = hw.get_health_rollup()
    temps = hw.get_temperatures()
    disks = hw.get_disk_info()
    psus = hw.get_psu_info()
    memory = hw.get_memory_info()
    cpu_percent = psutil.cpu_percent(interval=0.5)
    recorders = rec_manager.get_status()
    rec_info = rec_manager.get_recordings_info()

    retention = rec_manager.get_retention_settings()

    return render_template('index.html',
                           config=config,
                           system=system,
                           health=health,
                           temps=temps,
                           disks=disks,
                           psus=psus,
                           memory=memory,
                           cpu_percent=cpu_percent,
                           recorders=recorders,
                           rec_info=rec_info,
                           retention=retention)


@bp.route('/cameras/add', methods=['POST'])
@login_required
def add_camera():
    rec_manager = current_app.config['rec_manager']
    name = request.form.get('name', '').strip()
    rtsp_uri = request.form.get('rtsp_uri', '').strip()

    if not name or not rtsp_uri:
        flash('Name and RTSP URI are required.', 'danger')
        return redirect(url_for('web.index'))

    metadata = {}
    for fld in ('ip_address', 'manufacturer', 'model', 'location_name',
                'onvif_username', 'onvif_password'):
        v = (request.form.get(fld) or '').strip()
        if v:
            metadata[fld] = v
    for fld, cast in (('port', int), ('latitude', float), ('longitude', float)):
        raw = (request.form.get(fld) or '').strip()
        if raw:
            try:
                metadata[fld] = cast(raw)
            except ValueError:
                pass

    rec_manager.add_camera(name, rtsp_uri, metadata=metadata or None)
    flash(f'Camera "{name}" added and recording started.', 'success')
    return redirect(url_for('web.index'))


@bp.route('/cameras/<slug>/remove', methods=['POST'])
@login_required
def remove_camera(slug):
    rec_manager = current_app.config['rec_manager']
    rec_manager.remove_camera(slug)
    flash(f'Camera "{slug}" removed.', 'success')
    return redirect(url_for('web.index'))


@bp.route('/retention', methods=['POST'])
@login_required
def update_retention():
    rec_manager = current_app.config['rec_manager']
    retention_days = request.form.get('retention_days')
    max_storage_gb = request.form.get('max_storage_gb')

    rec_manager.update_retention(
        retention_days=retention_days,
        max_storage_gb=max_storage_gb,
    )
    flash(
        f'Retention updated: {rec_manager.retention_days} days, '
        f'{rec_manager.max_storage_gb} GB max.',
        'success',
    )
    return redirect(url_for('web.index'))


# --- Recordings / Playback ---

@bp.route('/recordings')
@login_required
def recordings():
    config = current_app.config['APP_CONFIG']
    rec_manager = current_app.config['rec_manager']
    rec_dir = config.RECORDINGS_DIR

    name_map = {c['slug']: c['name'] for c in rec_manager.get_camera_list()}

    cameras = {}
    if os.path.exists(rec_dir):
        for slug in sorted(os.listdir(rec_dir)):
            cam_dir = os.path.join(rec_dir, slug)
            if not os.path.isdir(cam_dir):
                continue
            in_progress = rec_manager.in_progress_filename(slug)
            is_rec = rec_manager.is_recording(slug)
            files = _list_finalised(cam_dir, in_progress, is_rec)
            if files:
                cameras[slug] = {
                    'display_name': name_map.get(slug, slug),
                    'files': files,
                }

    return render_template('recordings.html', cameras=cameras, config=config)


def _list_finalised(cam_dir, in_progress, is_recording):
    """Same hide rule as the JSON API — see web/api.py _finalised_segments."""
    from web.api import _finalised_segments
    return [
        {
            'name': name,
            'size_mb': round(stat.st_size / 1e6, 1),
            'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        }
        for name, stat in _finalised_segments(cam_dir, in_progress, is_recording)
    ]


@bp.route('/playback/<slug>')
@login_required
def playback(slug):
    config = current_app.config['APP_CONFIG']
    rec_manager = current_app.config['rec_manager']
    cam_dir = os.path.join(config.RECORDINGS_DIR, slug)

    if not os.path.isdir(cam_dir):
        abort(404)

    name_map = {c['slug']: c['name'] for c in rec_manager.get_camera_list()}
    display_name = name_map.get(slug, slug)

    in_progress = rec_manager.in_progress_filename(slug)
    is_rec = rec_manager.is_recording(slug)
    files = _list_finalised(cam_dir, in_progress, is_rec)

    selected = request.args.get('file', files[0]['name'] if files else None)
    return render_template('playback.html', slug=slug, camera_name=display_name,
                           files=files, selected=selected, config=config)


@bp.route('/playback/<slug>/files')
@login_required
def playback_files_json(slug):
    """JSON list of finalised recordings — UI polls this for auto-refresh."""
    config = current_app.config['APP_CONFIG']
    rec_manager = current_app.config['rec_manager']
    cam_dir = os.path.join(config.RECORDINGS_DIR, slug)
    if not os.path.isdir(cam_dir):
        return jsonify({'files': []})
    in_progress = rec_manager.in_progress_filename(slug)
    is_rec = rec_manager.is_recording(slug)
    return jsonify({'files': _list_finalised(cam_dir, in_progress, is_rec)})


@bp.route('/recordings/<slug>/<filename>/delete', methods=['POST'])
@login_required
def delete_recording(slug, filename):
    config = current_app.config['APP_CONFIG']
    rec_dir = os.path.realpath(config.RECORDINGS_DIR)
    cam_dir = os.path.realpath(os.path.join(config.RECORDINGS_DIR, slug))
    if not cam_dir.startswith(rec_dir):
        abort(403)
    if not filename.endswith('.mp4') or '/' in filename or '\\' in filename:
        abort(400)
    filepath = os.path.join(cam_dir, filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        flash(f'Rekaman "{filename}" dihapus.', 'success')
    else:
        flash('File tidak ditemukan.', 'danger')
    return redirect(url_for('web.recordings'))


@bp.route('/recordings/<slug>/<filename>')
@login_required
def serve_recording(slug, filename):
    config = current_app.config['APP_CONFIG']
    cam_dir = os.path.realpath(os.path.join(config.RECORDINGS_DIR, slug))
    rec_dir = os.path.realpath(config.RECORDINGS_DIR)
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


# --- JSON API ---

@bp.route('/api/status')
@login_required
def api_status():
    hw = current_app.config['hw_monitor']
    rec_manager = current_app.config['rec_manager']

    return jsonify({
        'system': hw.get_system_info(),
        'health': hw.get_health_rollup(),
        'temperatures': hw.get_temperatures(),
        'disks': hw.get_disk_info(),
        'psus': hw.get_psu_info(),
        'memory': hw.get_memory_info(),
        'recorders': rec_manager.get_status(),
        'recordings': rec_manager.get_recordings_info(),
    })
