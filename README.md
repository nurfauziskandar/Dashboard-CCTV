# Dashboard CCTV - Pelco Integration

Aplikasi dashboard monitoring CCTV berbasis Flask untuk mengintegrasikan sistem Pelco. Memantau kamera via ONVIF, memonitor server storage (suhu, HDD health), filter kamera aktif/non-aktif, dan penandaan lokasi kamera pada peta.

---

## Daftar Isi

- [Arsitektur Sistem](#arsitektur-sistem)
- [Protokol dan Port](#protokol-dan-port)
- [Persyaratan](#persyaratan)
- [Instalasi](#instalasi)
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
- [Troubleshooting](#troubleshooting)
- [API Endpoints](#api-endpoints)
- [Struktur Database](#struktur-database)

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

```
Dashboard Server --> Kamera Pelco:
  ALLOW TCP 80   (ONVIF SOAP)
  ALLOW TCP 554  (RTSP stream)
  ALLOW UDP 3702 (WS-Discovery, opsional)

Dashboard Server --> iDRAC VX Storage:
  ALLOW TCP 443  (Redfish HTTPS)

Dashboard Server --> Endura NSM5200:
  ALLOW UDP 161  (SNMP)

Browser --> Dashboard Server:
  ALLOW TCP 5000 (development)
  ALLOW TCP 80   (production via Nginx)
  ALLOW TCP 443  (production via Nginx + SSL)
```

---

## Persyaratan

### Software

| Software | Versi Minimum | Keterangan |
|----------|---------------|------------|
| Python | 3.10+ | Diuji pada 3.13 |
| pip | 21+ | Package manager |
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

## Instalasi

### 1. Clone Repository

```bash
git clone https://github.com/nurfauziskandar/Dashboard-CCTV.git
cd Dashboard-CCTV
```

### 2. Buat Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
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

```bash
# Secret Key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Fernet Key (untuk enkripsi password ONVIF di database)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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

### Mode Demo (Development)

```bash
python3 run.py
```

Buka http://localhost:5000 -- otomatis terisi 12 kamera demo + 3 server demo.

### Mode Production

```bash
FLASK_ENV=production python3 run.py
```

Pada mode production, dashboard dimulai kosong. Tambahkan kamera dan server melalui UI.

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
| HDD Temp | < 40C | 40-50C | > 50C |
| Disk Health | OK | Warning | Critical |
| Disk Usage | < 70% | 70-90% | > 90% |
| PSU Health | OK | Warning | Critical |

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

Flask-APScheduler menjalankan dua job berkala:

| Job | Interval Default | Fungsi |
|-----|-----------------|--------|
| `poll_cameras` | 60 detik | Probe semua kamera via ONVIF, update status |
| `poll_servers` | 120 detik | Cek health semua server, update data HDD |

Interval dapat dikonfigurasi di `config.py`:

```python
CAMERA_POLL_INTERVAL = 30   # Poll kamera tiap 30 detik
SERVER_POLL_INTERVAL = 60   # Poll server tiap 60 detik
```

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

### Database error setelah update

```bash
# Reset database (HATI-HATI: menghapus semua data)
rm instance/dashboard.db
python3 run.py  # Database akan dibuat ulang
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
| POST | `/cameras/<id>/delete` | Hapus kamera |
| GET | `/cameras/api/list` | JSON: semua kamera |
| GET | `/cameras/api/list?status=active` | JSON: kamera aktif |
| POST | `/cameras/api/<id>/refresh` | JSON: probe ulang satu kamera |
| GET | `/cameras/api/discover` | JSON: scan jaringan ONVIF |

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
