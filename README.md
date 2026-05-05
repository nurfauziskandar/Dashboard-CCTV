# Dashboard CCTV - Pelco Integration

Aplikasi dashboard monitoring CCTV berbasis Flask untuk mengintegrasikan sistem Pelco. Memantau kamera via ONVIF, memonitor server storage (suhu, HDD health), filter kamera aktif dan non-aktif, dan penandaan lokasi kamera pada peta.

---

## Daftar Isi

- [Default Credentials](#default-credentials)
- [Arsitektur Sistem](#arsitektur-sistem)
- [Integrasi Dashboard ↔ Server-Storage](#integrasi-dashboard--server-storage)
- [Protokol dan Port](#protokol-dan-port)
- [Persyaratan](#persyaratan)
- [Instalasi](#instalasi)
  - [1. Clone Repository](#1-clone-repository)
  - [2. Install Python](#2-install-python)
  - [3. Install ffmpeg](#3-install-ffmpeg-wajib-untuk-server-storage)
  - [4. Install Python Dependencies](#4-install-python-dependencies)
  - [5. Konfigurasi File .env](#5-konfigurasi-file-env)
- [Konfigurasi](#konfigurasi)
- [Menjalankan Aplikasi](#menjalankan-aplikasi)
- [Implementasi Production dengan Pelco](#implementasi-production-dengan-pelco)
  - [1. Persiapan Jaringan](#1-persiapan-jaringan)
  - [2. Konfigurasi Kamera Pelco (ONVIF)](#2-konfigurasi-kamera-pelco-onvif)
  - [3. Konfigurasi Pelco Storage Server](#3-konfigurasi-pelco-storage-server)
  - [4. Environment Variables Production](#4-environment-variables-production)
  - [5. Deployment dengan Gunicorn](#5-deployment-dengan-gunicorn)
  - [6. Reverse Proxy (Nginx)](#6-reverse-proxy-nginx)
  - [7. Systemd Service](#7-systemd-service)
- [Cara Kerja Integrasi](#cara-kerja-integrasi)
  - [ONVIF Camera Probe](#onvif-camera-probe)
  - [Server Hardware Monitoring](#server-hardware-monitoring)
  - [Background Polling](#background-polling)
  - [Summary Report](#summary-report)
- [Troubleshooting](#troubleshooting)
- [API Endpoints](#api-endpoints)
- [Struktur Database](#struktur-database)

---

## Default Credentials

> **PENTING:** Ganti semua default ini sebelum production.

### Dashboard CCTV (port 5000)

| Field | Default | Env Var |
|-------|---------|---------|
| Username | `admin` | `ADMIN_USERNAME` |
| Password | `admin123` | `ADMIN_PASSWORD` (atau `ADMIN_PASSWORD_HASH`) |

Login URL: `http://<host>:5000/login`

### Server-Storage Management UI (port 8080)

| Field | Default | Env Var |
|-------|---------|---------|
| Username | `admin` | `ADMIN_USERNAME` |
| Password | `storage123` | `ADMIN_PASSWORD` (atau `ADMIN_PASSWORD_HASH`) |

Login URL: `http://<storage-host>:8080/login`

### Integration API Token (Dashboard → Storage)

Kedua aplikasi harus pakai token yang sama:

```bash
# Generate token (sekali)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Set di kedua aplikasi
export STORAGE_API_TOKEN='<generated-token>'
```

Default placeholder (HARUS diganti):
`change-me-storage-api-token-min-32-chars-long-please`

### URL Signing Secret (server-storage MP4 serve)

```bash
export URL_SIGNING_SECRET='<random-48-chars>'
```

Signed URL TTL default: 300 detik (5 menit), set via `SIGNED_URL_TTL_SECONDS`.

### Generate Password Hash (production)

Hindari plaintext password di env. Generate hash dulu:

```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-strong-password'))"
```

Lalu set `ADMIN_PASSWORD_HASH=<hash>` (override `ADMIN_PASSWORD`).

---

## Arsitektur Sistem

```mermaid
graph TD
    Browser["Browser\nBootstrap 5.3 + Leaflet.js + FontAwesome 6.5"]

    subgraph Flask["Flask Application · Port 5000"]
        direction TB

        subgraph Routing["Routing Layer"]
            R1["dashboard.py\n/ · /dashboard"]
            R2["cameras.py\n/cameras/*"]
            R3["servers.py\n/servers/*"]
        end

        subgraph Service["Service Layer"]
            CS["CameraService\nONVIF probe · polling · discovery"]
            SS["ServerService\nHealth poll · HDD/PSU upsert"]
        end

        subgraph Adapter["Adapter Layer"]
            ONVIF["ONVIFAdapter\nonvif-zeep library"]
            WSD["WSDiscovery\nUDP multicast scan"]
            HWMon["HardwareMonitor\nRouting berdasarkan server_type"]
            IDRAC["iDRAC Redfish Client\nrequests · REST JSON"]
            SNMP["SNMP Client\npysnmp · GET/WALK"]
        end

        subgraph Data["Data Layer"]
            Models["SQLAlchemy Models\nCamera · Server · HDD · PSU"]
        end

        Scheduler["APScheduler\npoll_cameras (60s) · poll_servers (120s)"]
    end

    DB[("SQLite\ninstance/dashboard.db")]

    subgraph Pelco["Perangkat Pelco"]
        CAM["Kamera Pelco\nSarix · Spectra · Optera"]
        VXS["VX Storage E-Series\nDell PowerEdge R740xd\niDRAC 9 built-in"]
        VXS_IDRAC["iDRAC 9 BMC\nManagement Controller"]
        NSM["Endura NSM5200\nNetwork Storage Manager"]
    end

    Browser -->|"HTTP/HTTPS\nPort 5000 (dev) · 80/443 (prod)"| Routing

    R1 & R2 --> CS
    R1 & R3 --> SS

    CS --> ONVIF
    CS --> WSD
    SS --> HWMon
    HWMon -->|"server_type = vxstorage"| IDRAC
    HWMon -->|"server_type = endura"| SNMP

    CS & SS --> Models
    Models --> DB
    Scheduler --> CS & SS

    ONVIF -->|"ONVIF/SOAP · HTTP\nTCP Port 80"| CAM
    ONVIF -->|"RTSP\nTCP Port 554"| CAM
    WSD -->|"WS-Discovery\nUDP Port 3702\nMulticast 239.255.255.250"| CAM

    VXS --- VXS_IDRAC
    IDRAC -->|"Redfish REST API\nHTTPS Port 443\nJSON over TLS"| VXS_IDRAC
    SNMP -->|"SNMP v2c/v3\nUDP Port 161"| NSM
```

### Alur Data

```mermaid
flowchart LR
    subgraph Input["Sumber Data"]
        A["ONVIF GetDeviceInformation\nModel · Firmware · Serial"]
        B["ONVIF GetStreamUri\nRTSP URL"]
        C["ONVIF GetSnapshotUri\nJPEG URL"]
        D["Redfish /Systems\nModel · Serial · Power · Health"]
        E["Redfish /Thermal\nInlet · Exhaust Temp"]
        F["Redfish /Storage/Drives\nDisk Health · Capacity · Temp"]
        G["Redfish /Power\nPSU Health · Watt"]
        H["SNMP hrStorageTable\nDisk Usage"]
    end

    subgraph Output["Database Tables"]
        T1[("camera")]
        T2[("server")]
        T3[("hdd")]
        T4[("psu")]
    end

    A & B & C --> T1
    D & E --> T2
    F & H --> T3
    G --> T4
```

---

## Integrasi Dashboard ↔ Server-Storage

Dashboard dan Server-Storage berjalan **terpisah** (host beda boleh). Pola integrasi:

```mermaid
flowchart LR
    subgraph A["Host A · Port 5000"]
        DASH[Dashboard CCTV]
    end

    CAM([Kamera CCTV])

    subgraph B["Host B · Port 8080"]
        STOR["Server-Storage\nREST API · MP4 · Signed URL"]
    end

    DASH -- Live RTSP --> CAM
    STOR -- Record RTSP --> CAM
    DASH -- POST /api/cameras --> STOR
    DASH -- GET /api/recordings --> STOR
```

### Cara kerja

1. **Live view** — Dashboard buka stream RTSP langsung ke kamera (low latency, ~0 ms tambahan).
2. **Auto-register** — Saat tambah kamera di dashboard, dashboard panggil `POST {STORAGE_URL}/api/cameras` dengan body `{name, rtsp_uri}`. Server-storage mulai recording.
3. **Playback** — Dashboard panggil `GET {STORAGE_URL}/api/recordings/<camera_name>`. Server-storage balas list MP4 + signed URL per file (HMAC-SHA256, expires 5 menit). Browser fetch MP4 langsung dari server-storage pakai signed URL (no proxy, hemat bandwidth dashboard).

### Flow Penambahan Kamera (langkah demi langkah)

```mermaid
flowchart TD
    A([User klik Add Camera]) --> B[Form Input\nname · ip · RTSP URI atau ONVIF creds · lat/lng]
    B --> C[POST /cameras/add]
    C --> D{add_mode}

    D -- rtsp --> E[Simpan stream_uri]
    D -- onvif --> F[ONVIFAdapter.probe\nGetDeviceInformation\nGetStreamUri\nGetSnapshotUri]

    E & F --> G[INSERT camera ke SQLite]

    G --> H{STORAGE_URL di-set?}
    H -- Tidak --> I([Selesai - standalone])
    H -- Ya --> J[StorageClient\nPOST /api/cameras\nname + rtsp_uri + X-API-Token]
    J --> K[Server-Storage CameraRecorder\nmulai rekam MP4]
    K --> L([Selesai])
```

**Sumber stream untuk masing-masing fungsi:**

| Fungsi | Sumber | Protokol | Keterangan |
|--------|--------|----------|------------|
| **Live view** (halaman detail kamera) | **Langsung dari kamera CCTV** | RTSP → MJPEG proxy | Dashboard buka koneksi RTSP ke kamera, convert frame ke MJPEG untuk browser. **Tidak melalui server-storage**. Latency rendah (~ratusan ms). |
| **Snapshot** (thumbnail di list) | Langsung dari kamera | RTSP single frame | Diambil on-demand dari kamera, di-encode jadi JPEG. |
| **Recording** (file MP4) | Langsung dari kamera | RTSP → MP4 (H.264) | Server-storage punya koneksi RTSP **terpisah** ke kamera, rekam segmen MP4 (default 5 menit/file). |
| **Playback** (halaman playback) | **Dari server-storage** | HTTP MP4 + signed URL | Dashboard request list rekaman ke server-storage, browser fetch MP4 langsung dari server-storage (HMAC-SHA256 signed URL, TTL 5 menit). **Tidak melalui dashboard sebagai proxy**. |

Singkatnya:
- **Live = direct RTSP ke kamera** (low latency, real-time).
- **Playback = MP4 dari server-storage** (sudah di-segmentasi & disimpan).
- Kamera dirakam dua koneksi RTSP independen: satu untuk live (dashboard), satu untuk record (server-storage). Beban di kamera: 2 stream concurrent.

**Catatan codec recording:** server-storage menggunakan **ffmpeg subprocess** sebagai backend utama recording. Setiap segmen direkam dengan `libx264` + `-movflags +faststart` sehingga MP4 hasilnya dapat langsung diputar di semua browser (Chrome, Firefox, Safari) via HTTP tanpa buffering penuh. Jika `ffmpeg` tidak ada di PATH, fallback ke OpenCV VideoWriter dengan post-process remux; file tetap ter-rekam tapi mungkin tidak bisa diputar di Firefox. **Pastikan ffmpeg ter-install di host server-storage.**

### Setup Integrasi

**Server-storage (host B):**

```bash
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='ganti-password-storage'
export STORAGE_API_TOKEN='<token-yang-sama-dengan-dashboard>'
export URL_SIGNING_SECRET='<secret-random-48-chars>'

cd server-storage
python3 run.py
# → http://<host-B>:8080
```

**Dashboard (host A):**

```bash
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='ganti-password-dashboard'
export STORAGE_URL='http://<host-B>:8080'
export STORAGE_API_TOKEN='<token-yang-sama-dengan-server-storage>'

FLASK_ENV=production python3 run.py
# → http://<host-A>:5000
```

### REST API (Server-Storage)

Semua endpoint butuh `X-API-Token: <token>` atau `Authorization: Bearer <token>`.

| Method | Path | Body / Response |
|--------|------|-----------------|
| `GET`  | `/api/health` | `{"status": "ok"}` (no auth) |
| `GET`  | `/api/cameras` | `{"cameras": [...]}` |
| `POST` | `/api/cameras` | Body: `{"name", "rtsp_uri"}` |
| `DELETE` | `/api/cameras/<name>` | — |
| `GET`  | `/api/recordings/<camera_name>` | `{"camera", "files": [{name, size_mb, modified, url}]}` |
| `GET`  | `/api/recordings/<cam>/<file>?expires=<ts>&sig=<hex>` | MP4 stream (range support) — signed URL only, no token |

### Disable Storage Integration

Kalau `STORAGE_URL` tidak di-set, dashboard pakai mode standalone:
- Auto-register di-skip
- Tombol "Playback" tetap ada tapi flash warning kalau diklik

---

## Protokol dan Port

Berikut adalah semua protokol dan port yang digunakan oleh sistem:

### Port yang Digunakan Dashboard

| Port | Protokol | Arah | Keterangan |
|------|----------|------|------------|
| **5000** | TCP/HTTP | Inbound | Flask development server. Client browser mengakses dashboard melalui port ini. Di production, Gunicorn listen di port ini dan Nginx melakukan reverse proxy dari port 80/443. |
| **80** | TCP/HTTP | Inbound (prod) | Nginx reverse proxy menerima request dari browser dan meneruskan ke Gunicorn di port 5000. |
| **443** | TCP/HTTPS | Inbound (prod) | Nginx reverse proxy dengan SSL termination (opsional, untuk akses HTTPS). |

### Port ke Kamera Pelco

| Port | Protokol | Arah | Keterangan |
|------|----------|------|------------|
| **80** | TCP/HTTP | Outbound | **ONVIF SOAP** -- Dashboard mengirim request SOAP/XML ke kamera untuk `GetDeviceInformation`, `GetProfiles`, `GetStreamUri`, `GetSnapshotUri`. Ini adalah port utama komunikasi ONVIF. Beberapa kamera menggunakan port custom (8080, 8899). |
| **554** | TCP/RTSP | Outbound | **RTSP Streaming** -- Real Time Streaming Protocol untuk mengambil video stream. URL format: `rtsp://<user>:<pass>@<ip>:554/stream1`. Digunakan saat menampilkan stream URI di detail kamera. |
| **3702** | UDP/Multicast | Outbound | **WS-Discovery** -- Web Services Dynamic Discovery. Dashboard mengirim probe ke multicast address `239.255.255.250:3702` untuk menemukan kamera ONVIF di jaringan secara otomatis. Membutuhkan dukungan multicast di switch/router. |

### Port ke Pelco VX Storage (iDRAC)

| Port | Protokol | Arah | Keterangan |
|------|----------|------|------------|
| **443** | TCP/HTTPS | Outbound | **Dell iDRAC Redfish REST API** -- Dashboard mengirim HTTP GET request ke iDRAC BMC (Baseboard Management Controller) melalui HTTPS dengan basic auth. Semua data hardware (system info, thermal, storage, power) diambil melalui endpoint JSON di `https://<idrac_ip>/redfish/v1/...`. Sertifikat SSL self-signed diabaikan (`verify=False`). |

**Redfish Endpoints yang Digunakan:**

| Endpoint | Method | Data yang Diambil |
|----------|--------|-------------------|
| `/redfish/v1/Systems/System.Embedded.1` | GET | Model server, serial number, power state, health rollup |
| `/redfish/v1/Chassis/System.Embedded.1/Thermal` | GET | Inlet temperature, exhaust temperature dari sensor array |
| `/redfish/v1/Systems/System.Embedded.1/Storage` | GET | Daftar storage controller (PERC H740P, dll) |
| `/redfish/v1/.../Storage/{controller}/Drives/{id}` | GET | Per-drive: model, serial, capacity, health, media type (HDD/SSD), protocol (SAS/SATA/NVMe) |
| `/redfish/v1/Chassis/System.Embedded.1/Power` | GET | Per-PSU: model, health, power draw (watt), capacity (watt) |

### Port ke Pelco Endura NSM5200 (SNMP)

| Port | Protokol | Arah | Keterangan |
|------|----------|------|------------|
| **161** | UDP/SNMP | Outbound | **SNMP v2c/v3** -- Simple Network Management Protocol. Dashboard mengirim SNMP GET dan WALK request ke NSM5200 untuk mengambil informasi sistem dan storage. Autentikasi menggunakan community string (v2c) atau username/password (v3). |

**SNMP OIDs yang Digunakan:**

| OID | Nama | Operasi | Data yang Diambil |
|-----|------|---------|-------------------|
| `1.3.6.1.2.1.1.1.0` | sysDescr | GET | Deskripsi sistem (model, firmware) |
| `1.3.6.1.2.1.1.5.0` | sysName | GET | Hostname perangkat |
| `1.3.6.1.2.1.25.2.3.1.3` | hrStorageDescr | WALK | Daftar nama storage device |
| `1.3.6.1.2.1.25.2.3.1.5` | hrStorageSize | WALK | Total kapasitas per storage |
| `1.3.6.1.2.1.25.2.3.1.6` | hrStorageUsed | WALK | Kapasitas terpakai per storage |

### Ringkasan Firewall Rules

```mermaid
flowchart LR
    Browser[Browser] -->|TCP 5000/80/443| Dash[Dashboard Server]

    Dash -->|TCP 80 ONVIF| CAM[Kamera Pelco]
    Dash -->|TCP 554 RTSP| CAM
    Dash -.->|UDP 3702 WS-Discovery| CAM
    Dash -->|TCP 443 Redfish| IDRAC[iDRAC VxStorage]
    Dash -->|UDP 161 SNMP| NSM[Endura NSM5200]
```

---

## Persyaratan

### Software

| Software | Versi Minimum | Keterangan |
|----------|---------------|------------|
| Python | 3.10+ | Diuji pada 3.13 |
| pip | 21+ | Package manager |
| **ffmpeg** | 4.0+ | **Wajib di host server-storage** untuk recording H.264 + faststart (agar bisa diputar di semua browser termasuk Firefox). Install via package manager — lihat petunjuk di bawah. |
| Nginx | 1.18+ | Reverse proxy (production, opsional) |

### Hardware (Server Production)

- Server dashboard harus satu jaringan (atau routable) dengan kamera Pelco, iDRAC VX Storage, dan Endura NSM5200
- Port TCP 80 terbuka ke kamera (ONVIF SOAP)
- Port TCP 554 terbuka ke kamera (RTSP streaming)
- Port TCP 443 terbuka ke iDRAC VX Storage (Redfish REST API)
- Port UDP 161 terbuka ke Endura NSM5200 (SNMP)
- Port UDP 3702 multicast diizinkan (WS-Discovery, opsional)

### Kamera yang Didukung

Semua kamera Pelco yang mendukung ONVIF Profile S/T, termasuk:

| Seri | Tipe |
|------|------|
| Sarix IMP | Mini Dome |
| Sarix IME | Enhanced Mini Dome |
| Sarix IBP | Bullet |
| Sarix IBD | Dome |
| Sarix IXE | Dome / Box |
| Spectra Pro 2 | PTZ |
| Spectra Enhanced 7 | PTZ |
| Optera | Panoramic |

---

## Streaming Video

Dashboard mendukung dua mode streaming:

### Mode Production (Real RTSP)

`StreamService` (`app/services/stream_service.py`) mengkonversi stream RTSP dari kamera ke MJPEG untuk browser:

- Satu thread background (`_RTSPCapture`) per kamera yang sedang ditonton
- Auto-reconnect dengan exponential backoff jika koneksi RTSP terputus
- Thread-safe frame buffer
- Release otomatis saat tab browser ditutup

```
GET /cameras/<id>/stream        → MJPEG multipart/x-mixed-replace
GET /cameras/api/<id>/snapshot  → JPEG single frame
```

### Mode Demo (Synthetic Frames)

`FakeStreamService` (`app/services/demo/fake_stream.py`) membuat frame sintetis tanpa kamera nyata:

- Pattern grid pengawasan dengan efek scanline
- Objek bergerak dengan jalur acak
- Overlay timestamp dan nama kamera
- Indikator REC berkedip
- Frame rate ~10 FPS via Pillow + NumPy

---

## Mode Demo vs Production

| Aspek | Demo Mode | Production Mode |
|-------|-----------|-----------------|
| `FLASK_ENV` | `development` | `production` |
| `DEMO_MODE` | `True` | `False` |
| Kamera | 12 kamera Pelco fiktif (Jakarta) | Kamera ONVIF nyata via probe |
| Server | 3 server storage fiktif | iDRAC Redfish + SNMP nyata |
| Streaming | Frame sintetis (Pillow) | RTSP via OpenCV |
| HDD data | Acak realistis (SMART, suhu, health) | Data nyata dari iDRAC/SNMP |
| Auto-seed | Database terisi otomatis | Mulai kosong |

Demo mode menggunakan pola adapter injection -- `CameraService` dan `ServerService` menerima adapter berbeda saat startup tergantung `DEMO_MODE`. Logika bisnis identik di kedua mode.

---

## Instalasi

Sistem ini terdiri dari **dua aplikasi terpisah**:

| Aplikasi | Direktori | Port Default | Host |
|----------|-----------|-------------|------|
| **Dashboard CCTV** | `/` (root) | `5000` | Server monitoring |
| **Server-Storage** | `/server-storage/` | `8080` (web), `8443` (Redfish) | Server perekaman |

Keduanya bisa di-install di mesin yang sama (development) atau di mesin berbeda (production). Ikuti langkah berikut untuk masing-masing.

---

### 1. Clone Repository

```bash
git clone https://github.com/nurfauziskandar/Dashboard-CCTV.git
cd Dashboard-CCTV
```

### 2. Install Python

Pastikan Python 3.10+ sudah terinstall:

- **Windows**: Download dari [python.org](https://www.python.org/downloads/), centang **"Add Python to PATH"** saat instalasi
- **Linux (Debian/Ubuntu)**: `sudo apt install python3 python3-pip`
- **macOS**: `brew install python` atau download dari [python.org](https://www.python.org/downloads/)

Verifikasi:

```bash
python --version        # Windows
python3 --version       # Linux / macOS
```

### 3. Install ffmpeg (Wajib untuk Server-Storage)

ffmpeg digunakan oleh server-storage untuk merekam RTSP ke MP4 H.264 dengan faststart agar bisa diputar di semua browser (Chrome, Firefox, Safari).

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install -y ffmpeg
```

**Linux (RHEL/CentOS/Rocky):**
```bash
sudo dnf install -y ffmpeg   # butuh EPEL atau RPM Fusion
# atau:
sudo yum install -y epel-release && sudo yum install -y ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
```powershell
# Pakai Chocolatey
choco install ffmpeg

# Pakai winget
winget install Gyan.FFmpeg
```

Atau download manual dari [ffmpeg.org/download](https://ffmpeg.org/download.html), ekstrak, lalu tambahkan folder `bin/` ke PATH.

Verifikasi:
```bash
ffmpeg -version
```

> Jika ffmpeg tidak ada di PATH, server-storage akan jatuh ke mode OpenCV (fallback). File MP4 tetap ter-rekam tapi mungkin tidak bisa diputar di Firefox.

---

### 4. Install Python Dependencies

**Wajib pakai virtualenv** (`venv/`) supaya dependency tidak campur dengan system Python — kalau tidak, package seperti `WSDiscovery` tidak akan terbaca dan fitur Discover error `ModuleNotFoundError: No module named 'wsdiscovery'`.

#### Dashboard CCTV (direktori root)

**Linux / macOS:**
```bash
# Buat venv (sekali saja)
python3 -m venv venv

# Activate (setiap session terminal baru)
source venv/bin/activate

# Install
pip install -r requirements.txt
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Cek venv aktif (prompt harusnya prefix `(venv)`):
```bash
which python    # → .../Dashboard-CCTV/venv/bin/python
```

> Tanpa activate, jalankan langsung pakai path absolut:
> ```bash
> ./venv/bin/python run.py
> ```

#### Server-Storage (direktori `server-storage/`)

Server-storage memiliki venv dan requirements sendiri:

**Linux / macOS:**
```bash
cd server-storage

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (Command Prompt):**
```cmd
cd server-storage
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Windows (PowerShell):**
```powershell
cd server-storage
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

### 5. Konfigurasi File .env

Masing-masing aplikasi membaca file `.env` di direktorinya sendiri. Salin dari contoh:

**Dashboard CCTV:**
```bash
cp .env.example .env
# Edit .env sesuai kebutuhan
```

**Server-Storage:**
```bash
cd server-storage
cp .env.example .env
# Edit server-storage/.env sesuai kebutuhan
```

Isi minimal untuk production:

**`.env` (Dashboard CCTV):**
```env
FLASK_ENV=production
SECRET_KEY=<hasil secrets.token_hex(32)>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ganti-password-anda

# Integrasi ke server-storage
STORAGE_URL=http://<ip-server-storage>:8080
STORAGE_API_TOKEN=<token-sama-dengan-server-storage>
URL_SIGNING_SECRET=<secret-sama-dengan-server-storage>
```

**`server-storage/.env` (Server-Storage):**
```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ganti-password-storage

STORAGE_API_TOKEN=<token-sama-dengan-dashboard>
URL_SIGNING_SECRET=<secret-sama-dengan-dashboard>

# Direktori rekaman (default: server-storage/recordings/)
# RECORDINGS_DIR=/data/recordings

# Retensi (hari)
RETENTION_DAYS=7
MAX_STORAGE_GB=500
```

Generate token dan secret:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# jalankan dua kali — satu untuk STORAGE_API_TOKEN, satu untuk URL_SIGNING_SECRET
```

---

## Konfigurasi

Aplikasi memiliki dua mode:

| Mode | `FLASK_ENV` | `DEMO_MODE` | Keterangan |
|------|-------------|-------------|------------|
| Development | `development` | `True` | Menggunakan data demo (12 kamera, 3 server palsu) |
| Production | `production` | `False` | Terhubung ke kamera via ONVIF, VX Storage via iDRAC Redfish, Endura via SNMP |

### File `.env` (buat di root project)

```env
FLASK_ENV=production
SECRET_KEY=ganti-dengan-random-string-yang-panjang
FERNET_KEY=ganti-dengan-fernet-key
```

### Generate Secret Key dan Fernet Key

**Linux / macOS:**

```bash
# Secret Key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Fernet Key (untuk enkripsi password ONVIF di database)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Windows (Command Prompt / PowerShell):**

```cmd
python -c "import secrets; print(secrets.token_hex(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Konfigurasi Tambahan di `config.py`

| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `CAMERA_POLL_INTERVAL` | 60 | Interval polling status kamera (detik) |
| `SERVER_POLL_INTERVAL` | 120 | Interval polling health server (detik) |
| `DEFAULT_MAP_CENTER` | `[-6.2088, 106.8456]` | Koordinat default peta (Jakarta) |
| `DEFAULT_MAP_ZOOM` | 12 | Zoom level default peta |

Ubah `DEFAULT_MAP_CENTER` sesuai lokasi site:

```python
# Contoh untuk Surabaya
DEFAULT_MAP_CENTER = [-7.2575, 112.7521]

# Contoh untuk Bandung
DEFAULT_MAP_CENTER = [-6.9175, 107.6191]
```

---

## Menjalankan Aplikasi

### Dashboard CCTV

#### Mode Demo (Development)

Otomatis terisi 12 kamera demo + 3 server demo. Buka http://localhost:5000.

**Linux / macOS:**
```bash
source venv/bin/activate
python run.py
```

**Windows (Command Prompt):**
```cmd
venv\Scripts\activate
python run.py
```

**Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
python run.py
```

#### Mode Production

**Linux / macOS (first-time setup + seed):**
```bash
source venv/bin/activate
FLASK_ENV=production flask seed-sample --days 60   # seed kamera, server, 60 hari snapshot
FLASK_ENV=production python run.py
```

**Windows — jalankan `run_production.bat` (sudah include setup + seed):**
```
run_production.bat
```

Script ini otomatis:
1. Buat virtualenv jika belum ada
2. Install / update dependencies
3. Seed 60 hari historical snapshot (idempotent — aman dijalankan berulang)
4. Start dashboard

#### Flask CLI: `flask seed-sample`

Seed data dummy untuk kamera, server, dan snapshot historis. Aman dijalankan berulang — entri yang sudah ada di-skip.

```bash
# Seed semua (12 kamera + 3 server + 30 hari snapshot)
FLASK_APP=run.py FLASK_ENV=production flask seed-sample

# Seed dengan rentang historis lebih panjang
FLASK_APP=run.py FLASK_ENV=production flask seed-sample --days 60

# Seed snapshot saja (kamera & server sudah ada)
flask seed-sample --no-cameras --no-servers

# Seed kamera + server tanpa snapshot
flask seed-sample --no-snapshots
```

| Flag | Default | Keterangan |
|------|---------|------------|
| `--cameras / --no-cameras` | `--cameras` | Seed 12 kamera demo Pelco |
| `--servers / --no-servers` | `--servers` | Seed 3 server storage demo |
| `--snapshots / --no-snapshots` | `--snapshots` | Seed snapshot harian historis |
| `--days INTEGER` | `30` | Jumlah hari ke belakang yang di-backfill |

---

### Server-Storage

Server-storage menjalankan tiga layanan sekaligus dalam satu proses:

| Layanan | Port | Protokol | Keterangan |
|---------|------|----------|------------|
| Management Web UI | 8080 | HTTP | Browser management interface |
| Redfish API (iDRAC emulator) | 8443 | HTTPS | Diquery oleh dashboard untuk hardware metrics |
| RTSP Recorder | — | internal | Merekam stream kamera ke file MP4 |

**Linux / macOS:**
```bash
cd server-storage
source venv/bin/activate
python run.py
```

**Windows (Command Prompt):**
```cmd
cd server-storage
venv\Scripts\activate
python run.py
```

**Windows (PowerShell):**
```powershell
cd server-storage
.\venv\Scripts\Activate.ps1
python run.py
```

**Windows — buat `run.bat` di direktori `server-storage/`:**
```bat
@echo off
call venv\Scripts\activate
python run.py
pause
```

Setelah server-storage berjalan, terminal akan menampilkan:
```
============================================================
  Pelco Server Storage Simulator
============================================================
  Management UI     : http://192.168.1.x:8080
  Redfish API       : https://192.168.1.x:8443
  Recordings Dir    : .../recordings

  Dashboard-CCTV config:
    iDRAC IP        : 192.168.1.x:8443
    iDRAC Username  : root
    iDRAC Password  : calvin
============================================================
```

Gunakan nilai `iDRAC IP`, `Username`, dan `Password` tersebut saat menambahkan server di dashboard.

---

### Urutan Start (Recommended)

1. **Jalankan server-storage lebih dulu** — dashboard butuh URL storage saat startup
2. **Baru jalankan dashboard**

Untuk development di satu mesin, buka dua terminal:

```bash
# Terminal 1
cd server-storage && source venv/bin/activate && python run.py

# Terminal 2
source venv/bin/activate && FLASK_ENV=production python run.py
```

---

## Implementasi Production dengan Pelco

### 1. Persiapan Jaringan

```mermaid
graph TD
    CAM1["Kamera Pelco\n192.168.1.101"]
    CAM2["Kamera Pelco\n192.168.1.102"]
    CAM3["Kamera Pelco\n192.168.1.103"]
    NVR1["NVR-01\n192.168.1.10"]
    NVR2["NVR-02\n192.168.1.11"]
    NVR3["NVR-03\n192.168.1.12"]
    SW["Switch / Router"]
    DASH["Server Dashboard\n192.168.1.50 · Port 5000"]

    CAM1 --> SW
    CAM2 --> SW
    CAM3 --> SW
    NVR1 --> SW
    NVR2 --> SW
    NVR3 --> SW
    SW --> DASH
```

**Checklist jaringan:**

- [ ] Server dashboard satu subnet/VLAN dengan kamera dan NVR
- [ ] Port 80 terbuka dari server ke semua kamera (ONVIF HTTP)
- [ ] Port 554 terbuka dari server ke semua kamera (RTSP streaming)
- [ ] Port 3702 terbuka untuk UDP multicast (WS-Discovery, opsional)
- [ ] Firewall mengizinkan traffic antara server dashboard dan perangkat CCTV
- [ ] SNMP port 161 terbuka jika ingin monitoring via SNMP (opsional)

### 2. Konfigurasi Kamera Pelco (ONVIF)

#### Langkah A: Aktifkan ONVIF pada Kamera

1. Akses web interface kamera: `http://<IP_KAMERA>` (default: `192.168.0.20`)
2. Login dengan kredensial kamera:
   - Default username: `admin`
   - Default password: `admin` (beberapa model menggunakan `root`/`admin`)
3. Navigasi ke **System > Network > ONVIF**
4. Pastikan **ONVIF** dalam status **Enabled**
5. Catat atau set ONVIF user dan password

#### Langkah B: Verifikasi Konektivitas ONVIF

Tes dari server dashboard apakah kamera bisa diakses:

```bash
# Tes HTTP connectivity
curl -s -o /dev/null -w "%{http_code}" http://192.168.1.101/

# Tes ONVIF device info (menggunakan Python)
python3 -c "
from onvif import ONVIFCamera
cam = ONVIFCamera('192.168.1.101', 80, 'admin', 'admin')
info = cam.devicemgmt.GetDeviceInformation()
print(f'Manufacturer: {info.Manufacturer}')
print(f'Model: {info.Model}')
print(f'Firmware: {info.FirmwareVersion}')
print(f'Serial: {info.SerialNumber}')
"
```

#### Langkah C: Tes RTSP Stream

```bash
# Tes stream langsung (perlu ffmpeg/ffprobe)
ffprobe -v quiet -print_format json -show_streams \
  rtsp://admin:admin@192.168.1.101:554/stream1

# Atau cek dengan VLC
vlc rtsp://admin:admin@192.168.1.101:554/stream1
```

**Format RTSP URL Pelco:**

| Seri | Primary Stream | Secondary Stream |
|------|---------------|-----------------|
| Sarix (semua model) | `rtsp://<ip>:554/stream1` | `rtsp://<ip>:554/stream2` |
| Spectra | `rtsp://<ip>:554/stream1` | `rtsp://<ip>:554/stream2` |
| Digital Sentry NVR | `rtsp://<ip>/?deviceid=<id>` | - |

#### Langkah D: Tambahkan Kamera di Dashboard

1. Buka dashboard di browser
2. Navigasi ke **Cameras** > klik **Add Camera**
3. Isi form:
   - **Camera Name**: Nama deskriptif (contoh: "Lobby Utama")
   - **IP Address**: IP kamera (contoh: `192.168.1.101`)
   - **Port**: `80` (default ONVIF)
   - **ONVIF Username**: `admin`
   - **ONVIF Password**: password kamera
   - **Manufacturer**: Pelco
   - **Model**: isi model kamera
   - **Location Name**: lokasi fisik kamera
4. Klik pada peta untuk menandai lokasi kamera
5. Klik **Add Camera**

Sistem akan langsung melakukan ONVIF probe dan mengisi stream URI, snapshot URI, firmware, dan status.

#### Langkah E: Auto-Discovery (Opsional)

Jika kamera mendukung WS-Discovery:

1. Navigasi ke **Cameras**
2. Klik tombol **Discover**
3. Dashboard akan scan jaringan untuk perangkat ONVIF
4. Klik **Add** pada kamera yang ditemukan

> **Catatan**: WS-Discovery menggunakan UDP multicast (port 3702). Pastikan multicast diizinkan di jaringan.

### 3. Konfigurasi Pelco Storage Server

Dashboard mendukung dua jenis server storage Pelco:

| Produk | Tipe | Metode Monitoring | Port |
|--------|------|-------------------|------|
| **Pelco VX Storage** (E-Series) | `vxstorage` | Dell iDRAC Redfish REST API | HTTPS 443 (iDRAC) |
| **Pelco Endura NSM5200** | `endura` | SNMP v2c/v3 | UDP 161 |

#### 3A. Pelco VX Storage (via Dell iDRAC)

VX Storage E-Series adalah server Dell PowerEdge R740xd dengan Dell iDRAC built-in. Monitoring hardware dilakukan melalui **iDRAC Redfish REST API**.

**Persyaratan:**
- iDRAC harus memiliki IP address terpisah (biasanya di VLAN management)
- Kredensial iDRAC (default: `root` / `calvin`)
- Port HTTPS 443 terbuka dari dashboard server ke iDRAC IP

**Verifikasi konektivitas iDRAC:**

```bash
# Test iDRAC Redfish endpoint
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Systems/System.Embedded.1 | python3 -m json.tool

# Test temperature sensors
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Chassis/System.Embedded.1/Thermal | python3 -m json.tool

# Test physical disks
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Systems/System.Embedded.1/Storage | python3 -m json.tool

# Test power supplies
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Chassis/System.Embedded.1/Power | python3 -m json.tool
```

**Tambahkan di dashboard:**
1. Navigasi ke **Servers** > klik **Add Server**
2. Pilih type **Pelco VX Storage**
3. Isi Server IP (IP utama VxStorage)
4. Isi iDRAC IP, username, password
5. Klik **Add Server** -- dashboard langsung polling data via Redfish

> Detail Redfish endpoints lihat bagian [Protokol dan Port > Port ke Pelco VX Storage](#port-ke-pelco-vx-storage-idrac).

#### 3B. Pelco Endura NSM5200 (via SNMP)

Endura NSM5200 mendukung SNMP v2c dan v3 untuk monitoring.

**Aktifkan SNMP di NSM5200:**
1. Akses web interface NSM5200: `https://<IP_NSM5200>`
2. Navigasi ke **System > SNMP Settings**
3. Enable SNMP
4. Set community string (default: `public`)
5. Tambahkan IP dashboard server sebagai SNMP trap manager

**Tambahkan di dashboard:**
1. Navigasi ke **Servers** > klik **Add Server**
2. Pilih type **Pelco Endura (NSM5200)**
3. Isi IP address NSM5200
4. Isi SNMP community string
5. Klik **Add Server**

**Data monitoring NSM5200 via SNMP:**
- System description dan uptime
- Storage device listing (via HOST-RESOURCES-MIB hrStorageTable)
- SNMP traps untuk: HDD failures, fan failures, PSU failures, temperature alerts

> Detail SNMP OIDs lihat bagian [Protokol dan Port > Port ke Pelco Endura NSM5200](#port-ke-pelco-endura-nsm5200-snmp).

#### Interpretasi Indikator

| Indikator | Hijau | Kuning | Merah |
|-----------|-------|--------|-------|
| Inlet Temp (VxStorage) | < 35C | 35-42C | > 42C |
| Exhaust Temp (VxStorage) | < 55C | 55-70C | > 70C |
| Disk Health | OK | Warning | Critical |
| Disk Usage | < 70% | 70-90% | > 90% |
| PSU Health | OK | Warning | Critical |

#### Kalkulasi Health Status Server

Health status server (`OK` / `Warning` / `Critical`) dihitung dari dua sumber: **suhu** dan **penggunaan disk**. Whichever kondisi paling parah menentukan status akhir.

**1. Suhu Inlet (untuk VX Storage via iDRAC/emulator):**

```
inlet_temp > 42°C  → Critical
inlet_temp > 35°C  → Warning
inlet_temp ≤ 35°C  → OK
```

**2. Penggunaan Disk:**

Sistem iterasi semua drive. Jika ada satu drive yang melebihi threshold, status server naik:

```
(used_gb / capacity_gb) * 100 > 90%  → Critical
(used_gb / capacity_gb) * 100 > 80%  → Warning
(used_gb / capacity_gb) * 100 ≤ 80%  → OK
```

**Prioritas (yang paling parah menang):**

```
Critical > Warning > OK
```

Contoh: Jika suhu inlet 38°C (Warning) dan satu disk penuh 95% (Critical) → status server = **Critical**.

**Sumber data per server type:**

| Server Type | Sumber Suhu | Sumber Disk |
|-------------|-------------|-------------|
| VX Storage (iDRAC) | Redfish `/Thermal` → `ReadingCelsius` field bertanda "Inlet" | Redfish `/Storage/Drives` → `CapacityBytes` + `CapacityUsedBytes` |
| Server-Storage Emulator | psutil sensors / Apple SMC | psutil `disk_usage` |
| Endura NSM5200 (SNMP) | Tidak tersedia (selalu `None`) | SNMP hrStorageTable |

Jika semua sumber data tidak tersedia (server offline atau gagal polling), status ditampilkan sebagai `None` dan badge tidak muncul di UI.

**Kode referensi:**
- Dashboard: [`app/services/hardware_monitor.py`](app/services/hardware_monitor.py) — `_get_idrac_health()`, `_get_idrac_disks()`
- Server-Storage: [`server-storage/monitor/hardware.py`](server-storage/monitor/hardware.py) — `get_health_rollup()`
- Model thresholds: [`app/models/server.py`](app/models/server.py) — `inlet_temp_status`, `exhaust_temp_status`, `temp_status` (HDD)

### 4. Environment Variables Production

Buat file `.env` di root project:

```env
# Wajib
FLASK_ENV=production
SECRET_KEY=<hasil-dari-secrets.token_hex(32)>

# Opsional
FERNET_KEY=<hasil-dari-Fernet.generate_key()>
```

Atau export langsung:

```bash
export FLASK_ENV=production
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

### 5. Deployment dengan Gunicorn

Jangan gunakan `python3 run.py` di production. Gunakan Gunicorn:

```bash
pip install gunicorn
```

```bash
# Jalankan dengan 4 worker
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app('production')"

# Dengan timeout lebih panjang (untuk ONVIF probe yang lambat)
gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 "app:create_app('production')"
```

### 6. Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name cctv.internal.company.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeout untuk operasi yang memakan waktu (discovery, probe)
        proxy_read_timeout 120s;
    }

    location /static/ {
        alias /path/to/Dashboard-CCTV/app/static/;
        expires 7d;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/cctv /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 7. Systemd Service

Buat file `/etc/systemd/system/dashboard-cctv.service`:

```ini
[Unit]
Description=Dashboard CCTV - Pelco Monitor
After=network.target

[Service]
User=cctv
Group=cctv
WorkingDirectory=/opt/Dashboard-CCTV
Environment="FLASK_ENV=production"
Environment="SECRET_KEY=your-secret-key-here"
ExecStart=/opt/Dashboard-CCTV/venv/bin/gunicorn \
    -w 4 \
    -b 127.0.0.1:5000 \
    --timeout 120 \
    "app:create_app('production')"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard-cctv
sudo systemctl start dashboard-cctv
sudo systemctl status dashboard-cctv
```

---

## Cara Kerja Integrasi

### ONVIF Camera Probe

Ketika kamera ditambahkan atau di-polling, `ONVIFAdapter.probe()` melakukan:

```mermaid
sequenceDiagram
    participant S as camera_service
    participant A as ONVIFAdapter
    participant C as Kamera Pelco
    participant D as Database

    S->>A: probe(ip, port, user, pass)
    A->>C: GetDeviceInformation()
    C-->>A: Manufacturer, Model, Firmware, Serial
    A->>C: GetProfiles()
    C-->>A: Media profiles
    A->>C: GetStreamUri()
    C-->>A: rtsp://ip:554/stream1
    A->>C: GetSnapshotUri()
    C-->>A: http://ip/jpeg
    A-->>S: is_active=True, stream_uri, snapshot_uri
    S->>D: UPDATE camera SET is_active, stream_uri, last_seen

    note over A,C: Jika gagal (timeout/auth error)
    A-->>S: is_active=False
    S->>D: UPDATE camera SET is_active=False
```

### Server Hardware Monitoring

Ketika server di-polling, `HardwareMonitor` memilih metode berdasarkan `server_type`:

**VX Storage (iDRAC Redfish):**

```mermaid
sequenceDiagram
    participant S as server_service
    participant H as HardwareMonitor
    participant I as Dell iDRAC
    participant D as Database

    S->>H: get_server_health(server)
    H->>I: GET /redfish/v1/Systems/System.Embedded.1
    I-->>H: model, serial, power_state, health_rollup
    H->>I: GET /redfish/v1/Chassis/.../Thermal
    I-->>H: inlet_temp, exhaust_temp
    H-->>S: system health data

    S->>H: get_hdds(server)
    H->>I: GET /redfish/v1/Systems/.../Storage
    I-->>H: controller list
    loop Per controller
        H->>I: GET /redfish/v1/.../Drives/{id}
        I-->>H: model, serial, capacity, health, media_type
    end
    H-->>S: list disk data

    S->>H: get_psus(server)
    H->>I: GET /redfish/v1/Chassis/.../Power
    I-->>H: PSU name, model, health, power_watts
    H-->>S: list PSU data

    S->>D: UPSERT server + hdd + psu rows
```

**Endura NSM5200 (SNMP):**

```mermaid
sequenceDiagram
    participant S as server_service
    participant H as HardwareMonitor
    participant N as NSM5200 SNMP
    participant D as Database

    S->>H: get_server_health(server)
    H->>N: GET sysDescr (1.3.6.1.2.1.1.1.0)
    N-->>H: system description
    H->>N: GET sysName (1.3.6.1.2.1.1.5.0)
    N-->>H: system name
    H-->>S: basic health data

    S->>H: get_hdds(server)
    H->>N: WALK hrStorageDescr (1.3.6.1.2.1.25.2.3.1.3)
    N-->>H: storage device list
    H-->>S: list disk data

    S->>D: UPSERT server + hdd rows
```

### Background Polling

Flask-APScheduler menjalankan tiga job berkala:

| Job | Interval Default | Fungsi |
|-----|-----------------|--------|
| `poll_cameras` | 60 detik | Probe semua kamera via ONVIF, update status |
| `poll_servers` | 120 detik | Cek health semua server, update data HDD |
| `daily_snapshot` | Cron 00:00 | Simpan snapshot harian server + camera count untuk Summary Report |
| `sync_storage` | 60 detik | Sinkronisasi dua arah dashboard ↔ storage: push kamera baru ke storage, pull kamera dari storage yang belum ada di dashboard |

### Summary Report

Halaman `/summary` menampilkan grafik tren + log data semua server storage.

**Filter:**
- Date range (default: 30 hari terakhir)
- Storage server (per-server atau semua server)

**Grafik (Chart.js):**

| Grafik | Tipe | Keterangan |
|--------|------|------------|
| CPU Usage | Line | % penggunaan CPU per server per hari |
| Memory Usage | Line | % penggunaan memory per server per hari |
| Inlet Temperature | Line | Suhu inlet °C per server per hari |
| Server Health | Line | Jumlah server OK / Warning / Critical per hari |
| Camera Active Trend | Area | Kamera aktif vs non-aktif per hari |
| HDD Total | Line | Jumlah HDD per server per hari |

**Data Log:**
Satu flat table dengan pagination 25 baris/halaman + search real-time. Kolom: Tanggal, Server, Status, Health, CPU, MEM, Temp, HDD, Alerts, Cam Aktif, Cam Off.

**Export:**
- **Print / PDF** — tombol di kanan atas. Print dialog browser → "Save as PDF" menghasilkan A4 landscape dengan header laporan (periode, nama server, tanggal cetak). Sidebar/filter tersembunyi, semua grafik + tabel tampil.
- **CSV** — tombol di header tabel Data Log. Download `summary_<from>_to_<to>.csv`.

**Cara kerja snapshot:**
- APScheduler menulis ke tabel `status_snapshot` setiap tengah malam. Satu baris per (tanggal, server) + satu baris aggregate (server_id NULL) untuk total kamera.
- Jika hari ini belum ada snapshot tersimpan, halaman menampilkan data live saat ini.
- Historical data bisa di-backfill dengan `flask seed-sample --days N`.

Field per snapshot:

| Kolom | Sumber |
|-------|--------|
| `server_name` | Server.name |
| `is_online` | Server.is_online |
| `health_rollup` | OK / Warning / Critical |
| `inlet_temp` | Inlet temp (Celsius) |
| `cpu_usage` | CPU % |
| `memory_usage` | Memory % |
| `hdd_total` | jumlah HDD |
| `hdd_alerts` | jumlah HDD dengan health Warning/Critical |
| `cam_total` / `cam_active` / `cam_inactive` | total/aktif/inaktif kamera saat snapshot |

---

## Troubleshooting

### Kamera tidak terdeteksi / selalu Inactive

```bash
# 1. Cek konektivitas jaringan
ping 192.168.1.101

# 2. Cek port ONVIF terbuka
nc -zv 192.168.1.101 80

# 3. Cek port RTSP terbuka
nc -zv 192.168.1.101 554

# 4. Tes ONVIF manual
python3 -c "
from onvif import ONVIFCamera
cam = ONVIFCamera('192.168.1.101', 80, 'admin', 'admin')
print(cam.devicemgmt.GetDeviceInformation())
"
```

**Penyebab umum:**
- ONVIF belum diaktifkan di kamera
- Username/password ONVIF salah
- Firewall memblokir port 80/554
- Kamera tidak satu subnet dengan server

### Data server VxStorage tidak muncul

```bash
# 1. Cek konektivitas iDRAC
ping 192.168.1.110

# 2. Cek port HTTPS iDRAC terbuka
nc -zv 192.168.1.110 443

# 3. Tes Redfish API manual
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Systems/System.Embedded.1 \
  | python3 -m json.tool

# 4. Cek disk via Redfish
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Systems/System.Embedded.1/Storage \
  | python3 -m json.tool

# 5. Cek PSU via Redfish
curl -sk -u root:calvin \
  https://192.168.1.110/redfish/v1/Chassis/System.Embedded.1/Power \
  | python3 -m json.tool
```

**Penyebab umum:**
- iDRAC IP salah atau tidak bisa diakses dari jaringan aplikasi
- Username/password iDRAC salah (default: root/calvin)
- iDRAC firmware terlalu lama (butuh minimal iDRAC 8 untuk Redfish)
- HTTPS/port 443 diblokir firewall

### Data server Endura tidak muncul

```bash
# 1. Cek konektivitas SNMP
snmpwalk -v2c -c public 192.168.1.120 sysDescr.0

# 2. Cek storage table
snmpwalk -v2c -c public 192.168.1.120 hrStorageDescr

# 3. Cek sensor data
snmpwalk -v2c -c public 192.168.1.120 1.3.6.1.4.1.2696
```

**Penyebab umum:**
- SNMP belum diaktifkan di Endura NSM5200
- Community string salah (default: public)
- UDP port 161 diblokir firewall
- SNMP v3 diaktifkan tapi konfigurasi masih v2c

### Discovery tidak menemukan kamera

```bash
# Cek UDP multicast
python3 -c "
from wsdiscovery.discovery import ThreadedWSDiscovery
wsd = ThreadedWSDiscovery()
wsd.start()
services = wsd.searchServices()
print(f'Found {len(services)} devices')
for s in services:
    print(s.getXAddrs())
wsd.stop()
"
```

**Penyebab umum:**
- UDP multicast diblokir oleh switch/router
- IGMP snooping aktif tanpa konfigurasi yang benar
- Kamera di VLAN berbeda

### Recording tidak bisa diputar di Firefox

Firefox butuh MP4 dengan `moov` atom di awal file (faststart) dan codec H.264.

```bash
# Cek apakah ffmpeg tersedia di server-storage host
ffmpeg -version

# Jika tidak ada, install dulu:
# Ubuntu/Debian: sudo apt install ffmpeg
# macOS:         brew install ffmpeg
# Windows:       choco install ffmpeg

# Cek apakah file yang sudah ada bisa diputar (test dengan ffprobe)
ffprobe -v error -show_entries format_tags=major_brand \
  -of default=noprint_wrappers=1 recordings/<kamera>/<file>.mp4
```

Setelah ffmpeg ter-install dan server-storage di-restart, semua rekaman **baru** akan otomatis menggunakan ffmpeg backend dengan faststart. Rekaman lama yang sudah ada bisa dikonversi manual:

```bash
# Konversi satu file (tanpa re-encoding, hanya remux)
ffmpeg -i input.mp4 -c copy -movflags +faststart output.mp4

# Konversi semua file di semua kamera sekaligus
find recordings/ -name "*.mp4" -exec sh -c '
  ffmpeg -y -i "$1" -c copy -movflags +faststart "$1.tmp.mp4" \
  && mv "$1.tmp.mp4" "$1"
' _ {} \;
```

### Database error setelah update

```bash
# Reset database (HATI-HATI: menghapus semua data)
rm instance/dashboard.db
python3 run.py  # Database akan dibuat ulang
```

### Server-Storage tidak bisa diakses dari dashboard

```bash
# Cek server-storage berjalan
curl http://<ip-server-storage>:8080/api/health

# Cek Redfish API (dengan self-signed cert)
curl -sk https://<ip-server-storage>:8443/redfish/v1/ \
  -u root:calvin | python3 -m json.tool

# Cek token match
# Dashboard .env dan server-storage/.env harus punya STORAGE_API_TOKEN yang sama
curl http://<ip>:8080/api/cameras \
  -H "X-API-Token: <token>"
```

### ModuleNotFoundError: No module named 'wsdiscovery' (saat discovery)

```bash
# Pastikan venv aktif sebelum menjalankan dashboard
source venv/bin/activate
which python   # harus menunjuk ke venv, bukan system Python

# Install WSDiscovery di venv
pip install WSDiscovery

# Verifikasi
python -c "from wsdiscovery.discovery import ThreadedWSDiscovery; print('OK')"
```

---

## API Endpoints

### Cameras

| Method | Endpoint | Keterangan |
|--------|----------|------------|
| GET | `/cameras/` | Halaman daftar kamera (HTML) |
| GET | `/cameras/?status=active` | Filter kamera aktif |
| GET | `/cameras/?status=inactive` | Filter kamera non-aktif |
| GET | `/cameras/<id>` | Detail kamera (HTML) |
| GET | `/cameras/add` | Form tambah kamera (HTML) |
| POST | `/cameras/add` | Submit tambah kamera |
| POST | `/cameras/<id>/delete` | Hapus kamera dari dashboard (tidak menghapus dari storage) |
| POST | `/cameras/import_from_storage` | Import semua kamera dari storage server ke dashboard |
| GET | `/cameras/api/list` | JSON: semua kamera |
| GET | `/cameras/api/list?status=active` | JSON: kamera aktif |
| POST | `/cameras/api/<id>/refresh` | JSON: probe ulang satu kamera |
| GET | `/cameras/api/discover` | JSON: scan jaringan ONVIF |
| GET | `/cameras/<id>/playback_files` | JSON: daftar file rekaman untuk auto-refresh playback |

### Summary

| Method | Endpoint | Keterangan |
|--------|----------|------------|
| GET | `/summary/` | Halaman summary report + grafik (HTML) |
| GET | `/summary/?from=YYYY-MM-DD&to=YYYY-MM-DD` | Filter date range |
| GET | `/summary/?server_id=<id>` | Filter per storage server |
| GET | `/summary/export.csv` | Download CSV (mendukung filter `from`, `to`, `server_id`) |

### Servers

| Method | Endpoint | Keterangan |
|--------|----------|------------|
| GET | `/servers/` | Halaman daftar server (HTML) |
| GET | `/servers/<id>` | Detail server + tabel HDD (HTML) |
| POST | `/servers/add` | Tambah server |
| POST | `/servers/<id>/delete` | Hapus server |
| GET | `/servers/api/list` | JSON: semua server + HDD |
| POST | `/servers/api/<id>/refresh` | JSON: poll ulang satu server |

---

## Struktur Database

### Entity Relationship Diagram

```mermaid
erDiagram
    server ||--o{ hdd : "has"
    server ||--o{ psu : "has"
    server ||--o{ status_snapshot : "archived_as"

    camera {
        int id PK
        string name
        string ip_address
        int port
        string onvif_username
        string onvif_password
        string manufacturer
        string model
        string firmware
        string location_name
        float latitude
        float longitude
        string stream_uri
        string snapshot_uri
        boolean is_active
        datetime last_seen
    }

    server {
        int id PK
        string name
        string ip_address
        string description
        string server_type
        string idrac_ip
        string idrac_username
        string idrac_password
        string snmp_community
        string system_model
        string serial_number
        string power_state
        string health_rollup
        float inlet_temp
        float exhaust_temp
        float cpu_usage
        float memory_usage
        boolean is_online
        datetime last_checked
    }

    hdd {
        int id PK
        int server_id FK
        string device_name
        string slot
        string model
        string serial
        string media_type
        string protocol
        float capacity_gb
        float used_gb
        float temperature_c
        string health_status
        string state
        int predicted_life_left
        int power_on_hours
        int reallocated_sectors
        int pending_sectors
        datetime last_checked
    }

    psu {
        int id PK
        int server_id FK
        string name
        string model
        string health_status
        float power_watts
        float capacity_watts
        datetime last_checked
    }

    status_snapshot {
        int id PK
        date snapshot_date
        int server_id FK
        string server_name
        boolean is_online
        string health_rollup
        float inlet_temp
        float cpu_usage
        float memory_usage
        int hdd_total
        int hdd_alerts
        int cam_total
        int cam_active
        int cam_inactive
    }
```

### Tabel `camera`

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | INTEGER PK | Auto increment |
| name | VARCHAR(120) | Nama kamera |
| ip_address | VARCHAR(45) | IP kamera (unique) |
| port | INTEGER | Port ONVIF (default: 80) |
| onvif_username | VARCHAR(80) | Username ONVIF |
| onvif_password | VARCHAR(255) | Password ONVIF |
| manufacturer | VARCHAR(80) | Pabrikan (default: Pelco) |
| model | VARCHAR(80) | Model kamera |
| firmware | VARCHAR(80) | Versi firmware |
| location_name | VARCHAR(200) | Nama lokasi |
| latitude | FLOAT | Koordinat latitude |
| longitude | FLOAT | Koordinat longitude |
| stream_uri | VARCHAR(500) | RTSP stream URL (auto dari ONVIF) |
| snapshot_uri | VARCHAR(500) | JPEG snapshot URL (auto dari ONVIF) |
| is_active | BOOLEAN | Status aktif/non-aktif |
| last_seen | DATETIME | Terakhir kali kamera merespon |

### Tabel `server`

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | INTEGER PK | Auto increment |
| name | VARCHAR(120) | Nama server |
| ip_address | VARCHAR(45) | IP server (unique) |
| description | TEXT | Deskripsi |
| server_type | VARCHAR(30) | Tipe: `vxstorage`, `endura`, `other` |
| idrac_ip | VARCHAR(45) | IP iDRAC (khusus VxStorage) |
| idrac_username | VARCHAR(80) | Username iDRAC |
| idrac_password | VARCHAR(255) | Password iDRAC |
| snmp_community | VARCHAR(80) | SNMP community string (default: public) |
| system_model | VARCHAR(120) | Model sistem (dari iDRAC/SNMP) |
| serial_number | VARCHAR(80) | Serial number server |
| power_state | VARCHAR(20) | Status power (On/Off) |
| health_rollup | VARCHAR(20) | Health rollup: OK / Warning / Critical |
| inlet_temp | FLOAT | Suhu inlet (Celcius, VxStorage) |
| exhaust_temp | FLOAT | Suhu exhaust (Celcius, VxStorage) |
| cpu_usage | FLOAT | Penggunaan CPU (%) |
| memory_usage | FLOAT | Penggunaan memory (%) |
| is_online | BOOLEAN | Status online |
| last_checked | DATETIME | Terakhir kali di-poll |

### Tabel `hdd`

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | INTEGER PK | Auto increment |
| server_id | INTEGER FK | Referensi ke server |
| device_name | VARCHAR(50) | Nama device / drive ID |
| slot | VARCHAR(20) | Slot fisik (misal: Disk.Bay.0:Enclosure.Internal.0-1) |
| model | VARCHAR(120) | Model HDD/SSD |
| serial | VARCHAR(80) | Serial number |
| media_type | VARCHAR(20) | Tipe media: HDD, SSD |
| protocol | VARCHAR(20) | Protokol: SATA, SAS, NVMe |
| capacity_gb | FLOAT | Kapasitas (GB) |
| used_gb | FLOAT | Terpakai (GB) |
| temperature_c | FLOAT | Suhu drive (Celcius) |
| health_status | VARCHAR(20) | OK / Warning / Critical / Unknown |
| state | VARCHAR(30) | Status operasional (Online, Offline, etc.) |
| predicted_life_left | INTEGER | Prediksi sisa umur (%, khusus SSD) |
| power_on_hours | INTEGER | Total jam menyala |
| reallocated_sectors | INTEGER | Sektor rusak yang dipindahkan |
| pending_sectors | INTEGER | Sektor menunggu relokasi |
| last_checked | DATETIME | Terakhir kali di-poll |

### Tabel `psu`

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | INTEGER PK | Auto increment |
| server_id | INTEGER FK | Referensi ke server |
| name | VARCHAR(80) | Nama PSU (misal: PS1 Status) |
| model | VARCHAR(120) | Model PSU |
| health_status | VARCHAR(20) | OK / Warning / Critical / Unknown |
| power_watts | FLOAT | Daya terpakai saat ini (Watt) |
| capacity_watts | FLOAT | Kapasitas maksimum (Watt) |
| last_checked | DATETIME | Terakhir kali di-poll |

---

## Dependensi

| Paket | Versi Minimum | Fungsi |
|-------|---------------|--------|
| Flask | 3.0 | Web framework utama |
| Flask-SQLAlchemy | 3.1 | ORM untuk SQLite |
| Flask-APScheduler | 1.13 | Background polling jobs |
| onvif-zeep | 0.2.12 | ONVIF SOAP client |
| wsdiscovery | 2.0 | WS-Discovery multicast scan |
| zeep | 4.2 | SOAP library (dep onvif-zeep) |
| requests | 2.31 | HTTP client untuk iDRAC Redfish |
| pysnmp | 4.4 | SNMP client untuk Endura NSM5200 |
| opencv-python-headless | 4.9 | RTSP-to-MJPEG conversion |
| Pillow | 10.0 | Frame generation (demo mode) |
| numpy | 1.26 | Array operations untuk frame demo |
| cryptography | 42.0 | Enkripsi credentials (Fernet) |
| pytest | 8.0 | Testing |
| flake8 | 7.0 | Linting |

---

## Struktur Proyek

```
Dashboard-CCTV/
│
├── run.py                          # Entry point Dashboard CCTV
├── config.py                       # BaseConfig, DevelopmentConfig, ProductionConfig
├── requirements.txt                # Python dependencies Dashboard
├── .env.example                    # Contoh konfigurasi Dashboard
├── instance/
│   └── dashboard.db                # SQLite database (auto-dibuat)
│
├── app/                            # === DASHBOARD CCTV ===
│   ├── __init__.py                 # App factory, demo seeding, scheduler setup
│   ├── extensions.py               # db (SQLAlchemy), scheduler (APScheduler)
│   ├── models/
│   │   ├── camera.py               # Camera model
│   │   ├── server.py               # Server, HDD, PSU models
│   │   └── snapshot.py             # StatusSnapshot (Summary Report)
│   ├── services/
│   │   ├── camera_service.py       # CameraService (CRUD + polling)
│   │   ├── server_service.py       # ServerService (CRUD + polling)
│   │   ├── onvif_adapter.py        # Integrasi ONVIF nyata (onvif-zeep + WS-Discovery)
│   │   ├── hardware_monitor.py     # iDRAC Redfish + SNMP monitoring
│   │   ├── stream_service.py       # RTSP-to-MJPEG (OpenCV)
│   │   ├── storage_client.py       # REST client ke server-storage API
│   │   ├── snapshot_service.py     # Snapshot harian untuk Summary Report
│   │   └── demo/
│   │       ├── fake_cameras.py     # 12 kamera demo + FakeCameraAdapter
│   │       ├── fake_hardware.py    # 3 server demo + FakeHardwareMonitor
│   │       └── fake_stream.py      # Synthetic frame generator (Pillow)
│   ├── routes/
│   │   ├── __init__.py             # Registrasi blueprint
│   │   ├── auth.py                 # /login /logout
│   │   ├── dashboard.py            # GET / — overview stats + peta
│   │   ├── cameras.py              # /cameras/* + /cameras/api/*
│   │   ├── servers.py              # /servers/* + /servers/api/*
│   │   └── summary.py              # /summary/ + CSV export
│   ├── templates/
│   │   ├── base.html               # Master layout (sidebar + topbar)
│   │   ├── auth/login.html
│   │   ├── dashboard/index.html    # Stats cards + peta + server sidebar
│   │   ├── cameras/
│   │   │   ├── index.html          # Grid kamera + filter + Discover modal
│   │   │   ├── add.html            # Form tambah kamera + peta click-to-place
│   │   │   └── detail.html         # Live stream + info kamera + playback
│   │   ├── servers/
│   │   │   ├── index.html          # Kartu server + modal tambah server
│   │   │   └── detail.html         # Suhu + HDD table + PSU table
│   │   └── summary/
│   │       └── index.html          # Summary report per hari + CSV export
│   └── static/
│       ├── css/main.css            # CSS variables dark/light, semua custom style
│       └── js/theme-toggle.js      # Tema switcher + mobile sidebar toggle
│
└── server-storage/                 # === SERVER-STORAGE ===
    ├── run.py                      # Entry point Server-Storage (web + Redfish + recorder)
    ├── config.py                   # Config (auto-detect hardware model/serial)
    ├── requirements.txt            # Python dependencies Server-Storage
    ├── .env.example                # Contoh konfigurasi Server-Storage
    ├── cameras.json                # Daftar kamera yang direkam (persisten)
    ├── retention.json              # Setting retensi (persisten dari web UI)
    ├── recordings/                 # File MP4 rekaman (per kamera, per segmen)
    │   └── <nama-kamera>/
    │       └── <nama>_YYYYMMDD_HHMMSS.mp4
    ├── monitor/
    │   ├── hardware.py             # HardwareMonitor: CPU, memory, disk, temp, PSU
    │   └── smc_reader.py           # Apple SMC temperature reader (macOS)
    ├── recorder/
    │   └── stream_recorder.py      # CameraRecorder (ffmpeg/OpenCV) + RecordingManager
    ├── emulator/
    │   ├── redfish.py              # iDRAC Redfish API emulator (HTTPS)
    │   └── snmp_agent.py           # SNMP agent emulator (Endura mode)
    └── web/
        ├── auth.py                 # Login/logout web UI
        ├── routes.py               # Management web UI routes
        ├── api.py                  # REST API endpoints (/api/*)
        ├── templates/
        │   ├── base.html
        │   ├── index.html          # Dashboard: system info + disks + recorders
        │   ├── recordings.html     # Daftar rekaman per kamera + tombol hapus
        │   └── playback.html       # Video player + daftar segmen
        └── static/
```
