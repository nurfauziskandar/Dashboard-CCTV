# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Dashboard-CCTV

A Pelco CCTV monitoring dashboard built with Flask. Monitors cameras via ONVIF, tracks server storage health (temperature, HDD power), supports active/inactive camera filtering, and location marking on maps.

## Agent Workflow

This repo is configured with specialized sub-agent commands in `.claude/commands/`. Use them to delegate complex analysis tasks:

| Command | Purpose |
|---------|---------|
| `/project:research` | Deep research on technologies, APIs, and best practices |
| `/project:security-audit` | OWASP-based security audit and vulnerability scanning |
| `/project:ui-ux-review` | Accessibility, responsiveness, and usability review |
| `/project:architect` | System design, architecture decisions, trade-off analysis |
| `/project:code-review` | Code quality, patterns, and maintainability review |
| `/project:performance` | Performance profiling, bottleneck identification, optimization |

### Recommended workflow for complex features:
1. `/project:architect` -- design the approach first
2. `/project:research` -- research unknowns (APIs, libraries, protocols)
3. Implement the feature
4. `/project:code-review` -- review code quality
5. `/project:security-audit` -- check for vulnerabilities
6. `/project:ui-ux-review` -- validate frontend quality
7. `/project:performance` -- ensure performance is acceptable

## Tech Stack

- **Backend**: Python 3 + Flask 3 with Blueprints
- **Database**: SQLite via Flask-SQLAlchemy
- **Frontend**: Jinja2 SSR + Bootstrap 5.3 + FontAwesome 6.5
- **Maps**: Leaflet.js with OpenStreetMap tiles
- **Camera Integration**: ONVIF (onvif-zeep) + RTSP
- **Server Monitoring**: psutil + pySMART for HDD health
- **Background Jobs**: Flask-APScheduler for polling cameras/servers
- **Theme**: Dark/light mode via CSS variables + `data-bs-theme`

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (demo mode with fake data)
python3 run.py
# or: FLASK_ENV=development flask run

# Run production mode (real ONVIF/hardware)
FLASK_ENV=production python3 run.py

# Run tests
pytest

# Lint
flake8 app/
```

Default dev server: http://localhost:5000 (demo mode auto-seeds 12 cameras + 3 servers)

## Architecture

```
app/
  __init__.py          # App factory (create_app), demo seeding, scheduler setup
  extensions.py        # db (SQLAlchemy), scheduler (APScheduler)
  models/
    camera.py          # Camera model (name, IP, ONVIF creds, lat/lng, status)
    server.py          # Server + HDD models (temp, health, power hours, sectors)
  services/
    camera_service.py  # Camera CRUD + polling orchestration
    server_service.py  # Server CRUD + HDD health polling
    onvif_adapter.py   # Real ONVIF: probe, discover, snapshot via onvif-zeep
    hardware_monitor.py# Real HW: psutil temps + pySMART disk health
    demo/
      fake_cameras.py  # 12 demo Pelco cameras with Jakarta locations
      fake_hardware.py # 3 demo NVR servers with randomized HDD data
  routes/
    dashboard.py       # GET / -- overview with stats, map, camera table
    cameras.py         # /cameras/* -- list, detail, add, filter, API endpoints
    servers.py         # /servers/* -- list, detail, add, API endpoints
  templates/           # Jinja2 templates extending base.html
  static/
    css/main.css       # CSS variables for dark/light, all custom styles
    js/theme-toggle.js # Theme switcher + mobile sidebar toggle
config.py              # BaseConfig, DevelopmentConfig (DEMO_MODE=True), ProductionConfig
run.py                 # Entry point
```

## Key Patterns

- **Demo/Real dual mode**: `config.DEMO_MODE` flag switches service adapters. `CameraService` and `ServerService` inject either real adapters (ONVIF/psutil) or fake adapters at construction. Business logic is identical in both modes.
- **Blueprints**: `dashboard`, `cameras`, `servers` -- each has HTML views + `/api/*` JSON endpoints.
- **Background polling**: APScheduler runs `poll_all()` on both services at configured intervals. Dashboard always reads from DB (fast), never blocks on live ONVIF calls.
- **Templates**: All extend `base.html`. No emojis -- use FontAwesome icons only.
- **CSS theming**: All colors via CSS variables (`--bg-primary`, `--text-primary`, etc.) scoped to `[data-bs-theme="dark"]` and `[data-bs-theme="light"]`.
- **Maps**: Leaflet with click-to-place markers. Dark mode inverts tile layer.

## Pelco Integration (ONVIF)

All Pelco cameras are ONVIF Profile S/T compliant. The `ONVIFAdapter` uses:
- `onvif-zeep` for device info, stream URIs, snapshots
- `WSDiscovery` for network camera discovery
- RTSP format: `rtsp://<ip>:554/stream1` (Pelco Sarix default)
- Default credentials: `admin` / `admin`

## Key Conventions

- Use Flask Blueprints for route organization
- Templates extend `base.html` for consistent layout
- Keep business logic in `services/`, not in route handlers
- All camera/stream handling isolated in adapter modules
- No emojis in UI -- use FontAwesome icons
- Responsive mobile-first design with Bootstrap grid
