from flask import Blueprint, render_template, current_app, jsonify

bp = Blueprint('dashboard', __name__)


@bp.route('/')
def index():
    camera_service = current_app.config['camera_service']
    server_service = current_app.config['server_service']

    cameras = camera_service.get_all()
    cam_counts = camera_service.get_counts()
    servers = server_service.get_all()
    srv_summary = server_service.get_summary()

    return render_template(
        'dashboard/index.html',
        cameras=cameras,
        cam_counts=cam_counts,
        servers=servers,
        srv_summary=srv_summary,
        config=current_app.config,
    )


@bp.route('/api/summary')
def api_summary():
    """Lightweight JSON for client-side auto-refresh. Forces a quick health
    check on each server so offline state is reflected immediately."""
    camera_service = current_app.config['camera_service']
    server_service = current_app.config['server_service']

    # Probe all servers now (don't wait for the 120s poll cycle)
    for s in server_service.get_all():
        try:
            server_service.refresh_one(s.id)
        except Exception:
            pass

    cam_counts = camera_service.get_counts()
    servers = [
        {
            'id': s.id,
            'name': s.name,
            'ip_address': s.ip_address,
            'is_online': s.is_online,
            'inlet_temp': s.inlet_temp,
            'inlet_temp_status': s.inlet_temp_status if s.inlet_temp else None,
        }
        for s in server_service.get_all()
    ]
    return jsonify({
        'cam_counts': cam_counts,
        'servers': servers,
        'srv_summary': server_service.get_summary(),
    })
