"""Management Web UI for the Server Storage Simulator."""

import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file, abort

bp = Blueprint('web', __name__, url_prefix='/',
               template_folder='templates', static_folder='static')


@bp.route('/')
def index():
    hw = current_app.config['hw_monitor']
    rec_manager = current_app.config['rec_manager']
    config = current_app.config['APP_CONFIG']

    system = hw.get_system_info()
    health = hw.get_health_rollup()
    temps = hw.get_temperatures()
    disks = hw.get_disk_info()
    psus = hw.get_psu_info()
    memory = hw.get_memory_info()
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
                           recorders=recorders,
                           rec_info=rec_info,
                           retention=retention)


@bp.route('/cameras/add', methods=['POST'])
def add_camera():
    rec_manager = current_app.config['rec_manager']
    name = request.form.get('name', '').strip()
    rtsp_uri = request.form.get('rtsp_uri', '').strip()

    if not name or not rtsp_uri:
        flash('Name and RTSP URI are required.', 'danger')
        return redirect(url_for('web.index'))

    rec_manager.add_camera(name, rtsp_uri)
    flash(f'Camera "{name}" added and recording started.', 'success')
    return redirect(url_for('web.index'))


@bp.route('/cameras/<name>/remove', methods=['POST'])
def remove_camera(name):
    rec_manager = current_app.config['rec_manager']
    rec_manager.remove_camera(name)
    flash(f'Camera "{name}" removed.', 'success')
    return redirect(url_for('web.index'))


@bp.route('/retention', methods=['POST'])
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
def recordings():
    config = current_app.config['APP_CONFIG']
    rec_dir = config.RECORDINGS_DIR

    cameras = {}
    if os.path.exists(rec_dir):
        for cam_name in sorted(os.listdir(rec_dir)):
            cam_dir = os.path.join(rec_dir, cam_name)
            if not os.path.isdir(cam_dir):
                continue
            files = []
            for f in sorted(os.listdir(cam_dir), reverse=True):
                fpath = os.path.join(cam_dir, f)
                if os.path.isfile(fpath) and f.endswith('.mp4'):
                    stat = os.stat(fpath)
                    files.append({
                        'name': f,
                        'size_mb': round(stat.st_size / 1e6, 1),
                        'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'mtime': stat.st_mtime,
                    })
            if files:
                cameras[cam_name] = files

    return render_template('recordings.html', cameras=cameras, config=config)


@bp.route('/playback/<path:camera_name>')
def playback(camera_name):
    config = current_app.config['APP_CONFIG']
    cam_dir = os.path.join(config.RECORDINGS_DIR, camera_name)

    if not os.path.isdir(cam_dir):
        abort(404)

    files = []
    for f in sorted(os.listdir(cam_dir), reverse=True):
        fpath = os.path.join(cam_dir, f)
        if os.path.isfile(fpath) and f.endswith('.mp4'):
            stat = os.stat(fpath)
            files.append({
                'name': f,
                'size_mb': round(stat.st_size / 1e6, 1),
                'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            })

    selected = request.args.get('file', files[0]['name'] if files else None)
    return render_template('playback.html', camera_name=camera_name,
                           files=files, selected=selected, config=config)


@bp.route('/recordings/<path:camera_name>/<filename>')
def serve_recording(camera_name, filename):
    config = current_app.config['APP_CONFIG']
    # Prevent path traversal
    cam_dir = os.path.realpath(os.path.join(config.RECORDINGS_DIR, camera_name))
    rec_dir = os.path.realpath(config.RECORDINGS_DIR)
    if not cam_dir.startswith(rec_dir):
        abort(403)

    filepath = os.path.join(cam_dir, filename)
    if not os.path.isfile(filepath):
        abort(404)

    return send_file(filepath, mimetype='video/mp4', conditional=True)


# --- JSON API ---

@bp.route('/api/status')
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
