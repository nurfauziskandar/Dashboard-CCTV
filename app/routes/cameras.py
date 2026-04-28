from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, Response

bp = Blueprint('cameras', __name__, url_prefix='/cameras')


@bp.route('/')
def index():
    camera_service = current_app.config['camera_service']
    status_filter = request.args.get('status')
    cameras = camera_service.get_all(status=status_filter)
    counts = camera_service.get_counts()

    return render_template(
        'cameras/index.html',
        cameras=cameras,
        counts=counts,
        status_filter=status_filter,
        config=current_app.config,
    )


@bp.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        camera_service = current_app.config['camera_service']
        add_mode = request.form.get('add_mode', 'rtsp')

        if add_mode == 'rtsp':
            data = {
                'add_mode': 'rtsp',
                'name': request.form['name'],
                'ip_address': request.form.get('ip_address') or '',
                'port': int(request.form.get('port', 554)),
                'manufacturer': request.form.get('manufacturer', 'Pelco'),
                'model': request.form.get('model'),
                'location_name': request.form.get('location_name'),
                'stream_uri': request.form.get('stream_uri') or None,
            }
        else:
            data = {
                'add_mode': 'onvif',
                'name': request.form['name'],
                'ip_address': request.form.get('onvif_ip', ''),
                'port': int(request.form.get('onvif_port', 80)),
                'onvif_username': request.form.get('onvif_username'),
                'onvif_password': request.form.get('onvif_password'),
                'manufacturer': request.form.get('manufacturer', 'Pelco'),
                'model': request.form.get('model'),
                'location_name': request.form.get('location_name'),
            }

        lat = request.form.get('latitude')
        lng = request.form.get('longitude')
        if lat and lng:
            data['latitude'] = float(lat)
            data['longitude'] = float(lng)

        try:
            camera_service.create(data)
            flash('Camera added successfully.', 'success')
        except Exception as e:
            flash(f'Failed to add camera: {e}', 'danger')

        return redirect(url_for('cameras.index'))

    return render_template('cameras/add.html', config=current_app.config)


@bp.route('/<int:camera_id>')
def detail(camera_id):
    camera_service = current_app.config['camera_service']
    storage_client = current_app.config.get('storage_client')
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        flash('Camera not found.', 'danger')
        return redirect(url_for('cameras.index'))

    # Prefer storage's live MJPEG when available (avoids second RTSP session
    # from dashboard). Falls back to local stream if storage offline.
    live_url = None
    if storage_client and storage_client.enabled and camera.is_active:
        live_url = storage_client.live_url(camera.name)

    return render_template(
        'cameras/detail.html',
        camera=camera,
        config=current_app.config,
        live_url=live_url,
    )


@bp.route('/<int:camera_id>/playback')
def playback(camera_id):
    camera_service = current_app.config['camera_service']
    storage_client = current_app.config.get('storage_client')
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        flash('Camera not found.', 'danger')
        return redirect(url_for('cameras.index'))

    if not storage_client or not storage_client.enabled:
        flash('Storage backend belum dikonfigurasi (set STORAGE_URL).', 'warning')
        return redirect(url_for('cameras.detail', camera_id=camera_id))

    files = storage_client.list_recordings(camera.name)
    selected = request.args.get('file')
    if not selected and files:
        selected = files[0]['name']
    selected_file = next((f for f in files if f['name'] == selected), None)

    return render_template(
        'cameras/playback.html',
        camera=camera,
        files=files,
        selected_file=selected_file,
        storage_online=storage_client.health(),
        config=current_app.config,
    )


@bp.route('/<int:camera_id>/playback/files')
def playback_files(camera_id):
    """JSON list of finalised recordings — used by playback page polling
    for auto-refresh when ffmpeg closes a new segment."""
    camera_service = current_app.config['camera_service']
    storage_client = current_app.config.get('storage_client')
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    if not storage_client or not storage_client.enabled:
        return jsonify({'files': [], 'storage_online': False})
    files = storage_client.list_recordings(camera.name)
    return jsonify({'files': files, 'storage_online': True})


@bp.route('/<int:camera_id>/edit', methods=['GET', 'POST'])
def edit(camera_id):
    camera_service = current_app.config['camera_service']
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        flash('Camera not found.', 'danger')
        return redirect(url_for('cameras.index'))

    if request.method == 'POST':
        data = {
            'name': request.form['name'],
            'ip_address': request.form.get('ip_address', ''),
            'port': request.form.get('port'),
            'manufacturer': request.form.get('manufacturer'),
            'model': request.form.get('model'),
            'location_name': request.form.get('location_name'),
            'stream_uri': request.form.get('stream_uri'),
            'onvif_username': request.form.get('onvif_username'),
            'onvif_password': request.form.get('onvif_password'),
        }
        lat = request.form.get('latitude')
        lng = request.form.get('longitude')
        data['latitude'] = lat if lat else None
        data['longitude'] = lng if lng else None
        try:
            camera_service.update(camera_id, data)
            flash('Camera updated.', 'success')
        except Exception as e:
            flash(f'Failed to update camera: {e}', 'danger')
        return redirect(url_for('cameras.detail', camera_id=camera_id))

    return render_template('cameras/edit.html', camera=camera, config=current_app.config)


@bp.route('/<int:camera_id>/delete', methods=['POST'])
def delete(camera_id):
    camera_service = current_app.config['camera_service']
    camera_service.delete(camera_id)
    flash('Camera deleted.', 'success')
    return redirect(url_for('cameras.index'))


# --- JSON API ---

@bp.route('/api/list')
def api_list():
    camera_service = current_app.config['camera_service']
    status = request.args.get('status')
    cameras = camera_service.get_all(status=status)
    return jsonify([c.to_dict() for c in cameras])


@bp.route('/api/<int:camera_id>/refresh', methods=['POST'])
def api_refresh(camera_id):
    camera_service = current_app.config['camera_service']
    camera = camera_service.refresh_one(camera_id)
    if camera:
        return jsonify(camera.to_dict())
    return jsonify({'error': 'Camera not found'}), 404


@bp.route('/api/discover')
def api_discover():
    camera_service = current_app.config['camera_service']
    result = camera_service.discover()
    if isinstance(result, dict):
        return jsonify(result)
    return jsonify({'devices': result, 'error': None})


@bp.route('/import_from_storage', methods=['POST'])
def import_from_storage():
    """Pull every camera the storage server already knows about and create
    a Camera DB row for each one not yet present (matched by slug).

    Lets the user 'discover' cameras that were registered directly on the
    storage UI, plus pre-seed metadata (ip, port, model, location, etc.)
    that the storage already has."""
    storage_client = current_app.config.get('storage_client')
    camera_service = current_app.config['camera_service']
    if not storage_client or not storage_client.enabled:
        flash('Storage backend tidak terkonfigurasi (set STORAGE_URL).', 'warning')
        return redirect(url_for('cameras.index'))

    from app.services.storage_client import slugify

    storage_cams = storage_client.list_cameras()
    if not storage_cams:
        flash('Storage tidak punya kamera terdaftar (atau tidak bisa dijangkau).', 'info')
        return redirect(url_for('cameras.index'))

    existing_by_slug = {slugify(c.name): c for c in camera_service.get_all()}
    imported = 0
    patched = 0
    skipped = 0
    failed = 0
    for cam in storage_cams:
        slug = cam.get('slug') or slugify(cam.get('name', ''))
        if not slug or not cam.get('name') or not cam.get('rtsp_uri'):
            continue
        data = {
            'add_mode': 'rtsp',
            'name': cam['name'],
            'stream_uri': cam['rtsp_uri'],
            'ip_address': cam.get('ip_address') or '',
            'port': cam.get('port') or 554,
            'manufacturer': cam.get('manufacturer') or 'Pelco',
            'model': cam.get('model'),
            'location_name': cam.get('location_name'),
            'onvif_username': cam.get('onvif_username'),
            'onvif_password': cam.get('onvif_password'),
        }
        if cam.get('latitude') is not None:
            data['latitude'] = cam['latitude']
        if cam.get('longitude') is not None:
            data['longitude'] = cam['longitude']

        existing = existing_by_slug.get(slug)
        if existing is None:
            try:
                camera_service.create(data)
                imported += 1
            except Exception as exc:
                current_app.logger.warning(
                    'import_from_storage: failed for %s: %s', cam.get('name'), exc,
                )
                failed += 1
        elif not existing.stream_uri:
            # Camera exists but has no RTSP — patch it with storage data
            try:
                camera_service.update(existing.id, data)
                patched += 1
            except Exception as exc:
                current_app.logger.warning(
                    'import_from_storage: patch failed for %s: %s', cam.get('name'), exc,
                )
                failed += 1
        else:
            skipped += 1

    msg_parts = []
    if imported:
        msg_parts.append(f'{imported} kamera diimpor')
    if patched:
        msg_parts.append(f'{patched} kamera diperbarui (RTSP diisi dari storage)')
    if skipped:
        msg_parts.append(f'{skipped} sudah ada')
    if failed:
        msg_parts.append(f'{failed} gagal')
    flash(', '.join(msg_parts) or 'Tidak ada perubahan.',
          'success' if (imported or patched) else 'info')
    return redirect(url_for('cameras.index'))


# --- Streaming ---

@bp.route('/<int:camera_id>/stream')
def stream(camera_id):
    """MJPEG stream endpoint. Returns multipart JPEG frames."""
    camera_service = current_app.config['camera_service']
    stream_service = current_app.config['stream_service']
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera.is_active or not camera.stream_uri:
        return Response(status=503)

    return Response(
        stream_service.get_frame_generator(camera),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


@bp.route('/api/<int:camera_id>/snapshot')
def api_snapshot(camera_id):
    """Return a single JPEG snapshot from the camera."""
    camera_service = current_app.config['camera_service']
    stream_service = current_app.config['stream_service']
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera.is_active or not camera.stream_uri:
        return Response(status=503)

    frame = stream_service.get_snapshot(camera)
    if frame:
        return Response(frame, mimetype='image/jpeg')
    return Response(status=503)
