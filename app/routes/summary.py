import csv
import io
import json
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, current_app, Response

bp = Blueprint('summary', __name__, url_prefix='/summary')

_PALETTE = [
    '#4e9fdd', '#f5a623', '#7ed321', '#9b59b6', '#1abc9c',
    '#e74c3c', '#f39c12', '#2ecc71', '#3498db', '#e67e22',
]


def _parse_date(s, default):
    if not s:
        return default
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return default


def _build_chart_data(by_date, server_id_filter):
    """Return chart.js-ready dict from snapshot bundles.

    server_id_filter: '' = all servers, else int string.
    """
    sorted_dates_asc = sorted(by_date.keys())
    labels = [d.isoformat() for d in sorted_dates_asc]

    # Collect server id→name across all dates
    all_sids = {}
    for bundle in by_date.values():
        for s in bundle.get('servers', []):
            if s.server_id and s.server_name:
                all_sids[s.server_id] = s.server_name

    # Apply filter
    try:
        sid_filter = int(server_id_filter) if server_id_filter else None
    except (ValueError, TypeError):
        sid_filter = None
    if sid_filter:
        all_sids = {k: v for k, v in all_sids.items() if k == sid_filter}

    # Per-server time series
    servers_ts = {}
    for sid, sname in all_sids.items():
        cpu, mem, temp, hdd_alerts, hdd_total, online = [], [], [], [], [], []
        for d in sorted_dates_asc:
            row = next(
                (s for s in by_date.get(d, {}).get('servers', []) if s.server_id == sid),
                None,
            )
            cpu.append(round(row.cpu_usage, 1) if row and row.cpu_usage is not None else None)
            mem.append(round(row.memory_usage, 1) if row and row.memory_usage is not None else None)
            temp.append(round(row.inlet_temp, 1) if row and row.inlet_temp is not None else None)
            hdd_alerts.append(row.hdd_alerts if row and row.hdd_alerts is not None else 0)
            hdd_total.append(row.hdd_total if row and row.hdd_total is not None else 0)
            online.append(1 if row and row.is_online else 0)
        servers_ts[sname] = {
            'cpu': cpu, 'memory': mem, 'inlet_temp': temp,
            'hdd_alerts': hdd_alerts, 'hdd_total': hdd_total, 'online': online,
        }

    # Camera aggregate time series
    cam_active, cam_inactive = [], []
    for d in sorted_dates_asc:
        agg = by_date.get(d, {}).get('aggregate')
        cam_active.append(agg.cam_active if agg and agg.cam_active is not None else None)
        cam_inactive.append(agg.cam_inactive if agg and agg.cam_inactive is not None else None)

    # Health time series — count of OK/Warning/Critical servers per date
    # + per-date server name lists for tooltip detail
    health_ts = {'OK': [], 'Warning': [], 'Critical': []}
    health_servers = {}
    for d in sorted_dates_asc:
        counts = {'OK': 0, 'Warning': 0, 'Critical': 0}
        detail = {'OK': [], 'Warning': [], 'Critical': []}
        for s in by_date.get(d, {}).get('servers', []):
            if sid_filter and s.server_id != sid_filter:
                continue
            key = s.health_rollup if s.health_rollup in counts else 'OK'
            counts[key] += 1
            if s.server_name:
                detail[key].append(s.server_name)
        for k in health_ts:
            health_ts[k].append(counts[k])
        health_servers[d.isoformat()] = detail

    return {
        'labels': labels,
        'servers': servers_ts,
        'cameras': {'active': cam_active, 'inactive': cam_inactive},
        'health_ts': health_ts,
        'health_servers': health_servers,
    }


@bp.route('/')
def index():
    from app.models.server import Server as ServerModel
    from app.models.camera import Camera

    snapshot_service = current_app.config['snapshot_service']
    today = date.today()

    date_from = _parse_date(request.args.get('from'), today - timedelta(days=29))
    date_to = _parse_date(request.args.get('to'), today)
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    server_id_filter = request.args.get('server_id', '').strip()
    all_servers = ServerModel.query.order_by(ServerModel.name).all()

    inactive_cameras = (
        Camera.query
        .filter_by(is_active=False)
        .order_by(Camera.name)
        .all()
    )

    by_date_raw = snapshot_service.query_range(date_from, date_to)

    if date_from <= today <= date_to and today not in by_date_raw:
        by_date_raw.update(snapshot_service.query_live())

    chart_data = _build_chart_data(by_date_raw, server_id_filter)

    # Filter table rows by server (after chart data is built from full set)
    by_date = {
        d: {
            'aggregate': bundle['aggregate'],
            'servers': [
                s for s in bundle['servers']
                if not server_id_filter or str(s.server_id) == server_id_filter
            ],
        }
        for d, bundle in by_date_raw.items()
    }

    sorted_dates = sorted(by_date.keys(), reverse=True)

    return render_template(
        'summary/index.html',
        date_from=date_from,
        date_to=date_to,
        sorted_dates=sorted_dates,
        by_date=by_date,
        all_servers=all_servers,
        server_id_filter=server_id_filter,
        chart_data_json=json.dumps(chart_data),
        inactive_cameras=inactive_cameras,
        config=current_app.config,
    )


@bp.route('/export.csv')
def export_csv():
    snapshot_service = current_app.config['snapshot_service']
    today = date.today()
    date_from = _parse_date(request.args.get('from'), today - timedelta(days=29))
    date_to = _parse_date(request.args.get('to'), today)
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    server_id_filter = request.args.get('server_id', '').strip()

    by_date = snapshot_service.query_range(date_from, date_to)
    if today not in by_date and date_from <= today <= date_to:
        by_date.update(snapshot_service.query_live())

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        'date', 'server_name', 'is_online', 'health_rollup',
        'inlet_temp', 'cpu_usage', 'memory_usage',
        'hdd_total', 'hdd_alerts',
        'cam_total', 'cam_active', 'cam_inactive',
    ])
    for d in sorted(by_date.keys(), reverse=True):
        bundle = by_date[d]
        agg = bundle['aggregate']
        servers = bundle['servers']
        if server_id_filter:
            servers = [s for s in servers if str(s.server_id) == server_id_filter]
        if not servers:
            w.writerow([
                d.isoformat(), '(no servers)', '', '', '', '', '', '', '',
                agg.cam_total if agg else '',
                agg.cam_active if agg else '',
                agg.cam_inactive if agg else '',
            ])
        for s in servers:
            w.writerow([
                d.isoformat(),
                s.server_name or '',
                s.is_online,
                s.health_rollup or '',
                s.inlet_temp if s.inlet_temp is not None else '',
                s.cpu_usage if s.cpu_usage is not None else '',
                s.memory_usage if s.memory_usage is not None else '',
                s.hdd_total if s.hdd_total is not None else '',
                s.hdd_alerts if s.hdd_alerts is not None else '',
                s.cam_total if s.cam_total is not None else '',
                s.cam_active if s.cam_active is not None else '',
                s.cam_inactive if s.cam_inactive is not None else '',
            ])

    filename = f'summary_{date_from.isoformat()}_to_{date_to.isoformat()}.csv'
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )
