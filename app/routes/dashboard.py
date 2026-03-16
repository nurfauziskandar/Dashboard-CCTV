import json
from flask import Blueprint, render_template, current_app

bp = Blueprint('dashboard', __name__)


@bp.route('/')
def index():
    camera_service = current_app.config['camera_service']
    server_service = current_app.config['server_service']

    cameras = camera_service.get_all()
    cam_counts = camera_service.get_counts()
    servers = server_service.get_all()
    srv_summary = server_service.get_summary()

    cameras_json = json.dumps([c.to_dict() for c in cameras])

    return render_template(
        'dashboard/index.html',
        cameras=cameras,
        cameras_json=cameras_json,
        cam_counts=cam_counts,
        servers=servers,
        srv_summary=srv_summary,
        config=current_app.config,
    )
