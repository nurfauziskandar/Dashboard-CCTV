"""Capture daily snapshots and query them for the summary report."""
import logging
from datetime import date, datetime, timedelta
from app.extensions import db
from app.models.camera import Camera
from app.models.server import Server
from app.models.snapshot import StatusSnapshot

log = logging.getLogger(__name__)


class SnapshotService:

    def __init__(self, app):
        self.app = app

    def capture(self, target_date=None):
        """Upsert snapshot rows for target_date (default: today)."""
        if target_date is None:
            target_date = date.today()

        with self.app.app_context():
            cam_total = Camera.query.count()
            cam_active = Camera.query.filter_by(is_active=True).count()
            cam_inactive = cam_total - cam_active

            # Aggregate row (server_id=NULL) — camera totals only
            agg = StatusSnapshot.query.filter_by(
                snapshot_date=target_date, server_id=None,
            ).first()
            if agg is None:
                agg = StatusSnapshot(snapshot_date=target_date, server_id=None)
                db.session.add(agg)
            agg.cam_total = cam_total
            agg.cam_active = cam_active
            agg.cam_inactive = cam_inactive

            # Per-server rows
            servers = Server.query.all()
            for srv in servers:
                row = StatusSnapshot.query.filter_by(
                    snapshot_date=target_date, server_id=srv.id,
                ).first()
                if row is None:
                    row = StatusSnapshot(
                        snapshot_date=target_date, server_id=srv.id,
                    )
                    db.session.add(row)
                row.server_name = srv.name
                row.is_online = srv.is_online
                row.health_rollup = srv.health_rollup
                row.inlet_temp = srv.inlet_temp
                row.cpu_usage = srv.cpu_usage
                row.memory_usage = srv.memory_usage
                row.hdd_total = len(srv.hdds)
                row.hdd_alerts = sum(
                    1 for h in srv.hdds
                    if h.health_status in ('Critical', 'Warning')
                )
                row.cam_total = cam_total
                row.cam_active = cam_active
                row.cam_inactive = cam_inactive

            db.session.commit()
            log.info(
                'Snapshot captured for %s: %d server(s), %d camera(s)',
                target_date, len(servers), cam_total,
            )

    def query_range(self, date_from, date_to):
        """Return snapshots in [date_from, date_to] grouped by date."""
        rows = (
            StatusSnapshot.query
            .filter(StatusSnapshot.snapshot_date >= date_from)
            .filter(StatusSnapshot.snapshot_date <= date_to)
            .order_by(StatusSnapshot.snapshot_date.desc(),
                      StatusSnapshot.server_id.asc())
            .all()
        )

        by_date = {}
        for r in rows:
            by_date.setdefault(r.snapshot_date, {'aggregate': None, 'servers': []})
            if r.server_id is None:
                by_date[r.snapshot_date]['aggregate'] = r
            else:
                by_date[r.snapshot_date]['servers'].append(r)

        return by_date

    def query_live(self):
        """Build current-state snapshot without persisting (used as fallback)."""
        cam_total = Camera.query.count()
        cam_active = Camera.query.filter_by(is_active=True).count()
        servers = Server.query.all()

        agg = StatusSnapshot(
            snapshot_date=date.today(),
            server_id=None,
            cam_total=cam_total,
            cam_active=cam_active,
            cam_inactive=cam_total - cam_active,
        )
        server_rows = []
        for srv in servers:
            server_rows.append(StatusSnapshot(
                snapshot_date=date.today(),
                server_id=srv.id,
                server_name=srv.name,
                is_online=srv.is_online,
                health_rollup=srv.health_rollup,
                inlet_temp=srv.inlet_temp,
                cpu_usage=srv.cpu_usage,
                memory_usage=srv.memory_usage,
                hdd_total=len(srv.hdds),
                hdd_alerts=sum(
                    1 for h in srv.hdds
                    if h.health_status in ('Critical', 'Warning')
                ),
                cam_total=cam_total,
                cam_active=cam_active,
                cam_inactive=cam_total - cam_active,
            ))
        return {date.today(): {'aggregate': agg, 'servers': server_rows}}
