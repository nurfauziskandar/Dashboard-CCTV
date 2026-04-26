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
    camera = camera_service.get_by_id(camera_id)
    if not camera:
        flash('Camera not found.', 'danger')
        return redirect(url_for('cameras.index'))

    return render_template('cameras/detail.html', camera=camera, config=current_app.config)


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
    devices = camera_service.discover()
    return jsonify({'devices': devices})


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
