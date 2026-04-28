from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app

bp = Blueprint('servers', __name__, url_prefix='/servers')


@bp.route('/')
def index():
    server_service = current_app.config['server_service']
    servers = server_service.get_all()
    return render_template('servers/index.html', servers=servers, config=current_app.config)


@bp.route('/add', methods=['POST'])
def add():
    server_service = current_app.config['server_service']
    data = {
        'name': request.form['name'],
        'ip_address': request.form['ip_address'],
        'description': request.form.get('description'),
        'server_type': request.form.get('server_type', 'vxstorage'),
        'idrac_ip': request.form.get('idrac_ip'),
        'idrac_port': request.form.get('idrac_port', '443'),
        'idrac_username': request.form.get('idrac_username'),
        'idrac_password': request.form.get('idrac_password'),
        'snmp_community': request.form.get('snmp_community', 'public'),
    }
    try:
        server_service.create(data)
        flash('Server added successfully.', 'success')
    except Exception as e:
        flash(f'Failed to add server: {e}', 'danger')
    return redirect(url_for('servers.index'))


@bp.route('/<int:server_id>')
def detail(server_id):
    server_service = current_app.config['server_service']
    server = server_service.get_by_id(server_id)
    if not server:
        flash('Server not found.', 'danger')
        return redirect(url_for('servers.index'))
    return render_template('servers/detail.html', server=server, config=current_app.config)


@bp.route('/<int:server_id>/edit', methods=['GET', 'POST'])
def edit(server_id):
    server_service = current_app.config['server_service']
    server = server_service.get_by_id(server_id)
    if not server:
        flash('Server not found.', 'danger')
        return redirect(url_for('servers.index'))

    if request.method == 'POST':
        data = {
            'name': request.form['name'],
            'ip_address': request.form.get('ip_address', ''),
            'description': request.form.get('description'),
            'server_type': request.form.get('server_type', 'vxstorage'),
            'idrac_ip': request.form.get('idrac_ip'),
            'idrac_port': request.form.get('idrac_port'),
            'idrac_username': request.form.get('idrac_username'),
            'idrac_password': request.form.get('idrac_password'),
            'snmp_community': request.form.get('snmp_community', 'public'),
        }
        try:
            server_service.update(server_id, data)
            flash('Server updated.', 'success')
        except Exception as e:
            flash(f'Failed to update server: {e}', 'danger')
        return redirect(url_for('servers.detail', server_id=server_id))

    return render_template('servers/edit.html', server=server, config=current_app.config)


@bp.route('/<int:server_id>/delete', methods=['POST'])
def delete(server_id):
    server_service = current_app.config['server_service']
    server_service.delete(server_id)
    flash('Server deleted.', 'success')
    return redirect(url_for('servers.index'))


# --- JSON API ---

@bp.route('/api/list')
def api_list():
    server_service = current_app.config['server_service']
    servers = server_service.get_all()
    return jsonify([s.to_dict() for s in servers])


@bp.route('/api/<int:server_id>/refresh', methods=['POST'])
def api_refresh(server_id):
    server_service = current_app.config['server_service']
    server = server_service.refresh_one(server_id)
    if server:
        return jsonify(server.to_dict())
    return jsonify({'error': 'Server not found'}), 404
