import csv
import io
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, current_app, Response

bp = Blueprint('summary', __name__, url_prefix='/summary')


def _parse_date(s, default):
    if not s:
        return default
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return default


@bp.route('/')
def index():
    snapshot_service = current_app.config['snapshot_service']
    today = date.today()

    date_from = _parse_date(request.args.get('from'), today)
    date_to = _parse_date(request.args.get('to'), date_from)
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    by_date = snapshot_service.query_range(date_from, date_to)

    # Fallback: if today is in range and has no snapshot, fill from live
    if today in (date_from + timedelta(n) for n in range((date_to - date_from).days + 1)):
        if today not in by_date:
            live = snapshot_service.query_live()
            by_date.update(live)

    sorted_dates = sorted(by_date.keys(), reverse=True)

    return render_template(
        'summary/index.html',
        date_from=date_from,
        date_to=date_to,
        sorted_dates=sorted_dates,
        by_date=by_date,
        config=current_app.config,
    )


@bp.route('/export.csv')
def export_csv():
    snapshot_service = current_app.config['snapshot_service']
    today = date.today()
    date_from = _parse_date(request.args.get('from'), today)
    date_to = _parse_date(request.args.get('to'), date_from)
    if date_to < date_from:
        date_from, date_to = date_to, date_from

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
        if not bundle['servers']:
            w.writerow([
                d.isoformat(), '(no servers)', '', '', '', '', '', '', '',
                agg.cam_total if agg else '',
                agg.cam_active if agg else '',
                agg.cam_inactive if agg else '',
            ])
        for s in bundle['servers']:
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
