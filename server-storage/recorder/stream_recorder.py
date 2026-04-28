import os
import re
import sys
import time
import json
import shutil
import signal
import threading
import subprocess
import logging
from datetime import datetime, timedelta

import cv2

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == 'win32'

# Strip path-unsafe chars AND % so ffmpeg's strftime never misinterprets the name
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*%\x00-\x1f]')
_WHITESPACE = re.compile(r'\s+')
_MULTI_USCORE = re.compile(r'_+')


def slugify(name):
    """Backend identifier for a camera. Spaces become underscores, unsafe
    path chars are stripped, and runs of underscores collapsed. Used for
    folder names, dict keys, and API URL components — UI keeps the original
    display name."""
    if not name:
        return 'camera'
    s = name.strip()
    s = _WHITESPACE.sub('_', s)
    s = _UNSAFE_CHARS.sub('', s)
    s = _MULTI_USCORE.sub('_', s)
    s = s.strip('._-')
    return s or 'camera'


def _safe_filename(name):
    return _UNSAFE_CHARS.sub('_', name).strip('. ')


def _popen_kwargs():
    """Make the child its own process group so we can send a clean
    interrupt signal without taking down the parent.

    Windows needs CREATE_NEW_PROCESS_GROUP to receive CTRL_BREAK_EVENT.
    POSIX uses start_new_session so SIGINT only hits the child group.
    """
    if _IS_WINDOWS:
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def _interrupt(proc, name):
    """Signal ffmpeg to flush trailers and exit. Cross-platform."""
    if _IS_WINDOWS:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except (ValueError, OSError) as exc:
            logger.warning('Recorder %s: CTRL_BREAK_EVENT failed (%s)', name, exc)
            return
    try:
        proc.send_signal(signal.SIGINT)
    except (ProcessLookupError, OSError):
        pass


def _ffmpeg_available():
    return shutil.which('ffmpeg') is not None


class CameraRecorder:
    """Records a single RTSP camera stream to segmented MP4 files.

    Uses ffmpeg subprocess when available (H.264 + faststart = all-browser
    compatible). Falls back to OpenCV VideoWriter with a post-process remux
    step when ffmpeg binary is not on PATH.
    """

    def __init__(self, slug, display_name, rtsp_uri, config):
        # slug = backend id (no spaces, used in paths/URLs)
        # display_name = user-facing label (kept verbatim for UI)
        self.slug = slug
        self.display_name = display_name
        self.name = slug  # legacy alias for log messages
        self.rtsp_uri = rtsp_uri
        self.config = config
        self._running = False
        self._thread = None
        self._current_file = None
        self._frames_written = 0
        self._status = 'stopped'
        self._error = None
        self._started_at = None
        self._proc = None          # active ffmpeg subprocess (if any)
        self._use_ffmpeg = _ffmpeg_available()
        # Last 30 stderr lines from ffmpeg, exposed via UI/API for diagnosis
        self._last_stderr = []
        # Filename ffmpeg is currently writing (parsed from "Opening 'X'" log)
        self._current_segment_file = None
        if self._use_ffmpeg:
            logger.info('Recorder %s: using ffmpeg backend', name)
        else:
            logger.warning(
                'Recorder %s: ffmpeg not found, using OpenCV backend '
                '(Firefox playback may not work)', name,
            )

    @property
    def status(self):
        return self._status

    @property
    def error(self):
        return self._error

    @property
    def current_file(self):
        return self._current_file

    @property
    def frames_written(self):
        return self._frames_written

    @property
    def last_stderr(self):
        return list(self._last_stderr)

    @property
    def current_segment_file(self):
        """Basename of the segment ffmpeg is currently writing, or None."""
        cur = self._current_segment_file
        return os.path.basename(cur) if cur else None

    def start(self):
        if self._running:
            return
        self._running = True
        self._status = 'starting'
        self._error = None
        self._started_at = datetime.now()
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        proc = self._proc
        if proc and proc.poll() is None:
            # Soft interrupt first so ffmpeg flushes the trailer (moov atom)
            # on the in-progress segment. terminate/kill leave it unplayable.
            _interrupt(proc, self.name)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                logger.warning('Recorder %s: interrupt timed out, escalating to terminate', self.name)
                try:
                    proc.terminate()
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    logger.warning('Recorder %s: terminate timed out, sending kill', self.name)
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        if self._thread:
            self._thread.join(timeout=20)
        self._status = 'stopped'

    # ------------------------------------------------------------------ #
    # Recording loop dispatcher
    # ------------------------------------------------------------------ #

    def _record_loop(self):
        if self._use_ffmpeg:
            self._record_loop_ffmpeg()
        else:
            self._record_loop_opencv()

    # ------------------------------------------------------------------ #
    # ffmpeg backend
    # ------------------------------------------------------------------ #

    def _record_loop_ffmpeg(self):
        """Single long-running ffmpeg per recorder using the segment muxer.

        Each segment is finalized as soon as it ends, so files always have
        a moov atom and are immediately playable. SIGINT on stop() flushes
        the in-progress segment cleanly.
        """
        reconnect_delay = 2
        while self._running:
            cam_dir, pattern = self._segment_pattern()
            self._current_file = pattern
            self._status = 'recording'
            self._error = None

            fps = self.config.VIDEO_FPS
            seg_secs = max(int(self.config.SEGMENT_DURATION), 30)
            cmd = [
                'ffmpeg', '-y',
                '-hide_banner',
                '-loglevel', 'error',
                '-stats',
                '-rtsp_transport', 'tcp',
                '-fflags', '+genpts+discardcorrupt',
                '-i', self.rtsp_uri,
                '-map', '0:v:0',                  # video only
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-tune', 'zerolatency',
                '-profile:v', 'main',
                '-level', '3.1',
                '-pix_fmt', 'yuv420p',
                '-g', str(max(fps * 2, 30)),
                '-keyint_min', str(fps),
                '-sc_threshold', '0',
                '-crf', '23',
                '-r', str(fps),
                '-vsync', 'cfr',
                # Segment muxer: emit one finalized MP4 every seg_secs seconds.
                # Each file gets its own moov atom so it's playable on its own.
                '-f', 'segment',
                '-segment_time', str(seg_secs),
                '-segment_format', 'mp4',
                '-segment_format_options', 'movflags=+faststart',
                '-reset_timestamps', '1',
                '-strftime', '1',
                '-stats_period', '2',
                pattern,
            ]
            logger.info('Recorder %s: ffmpeg → %s (segments every %ds)',
                        self.name, pattern, seg_secs)

            stderr_lines = []
            self._last_stderr = stderr_lines  # share buffer for live API exposure

            def _read_stderr(pipe):
                buf = b''
                while True:
                    chunk = pipe.read(512)
                    if not chunk:
                        break
                    buf += chunk
                    while b'\r' in buf or b'\n' in buf:
                        for sep in (b'\n', b'\r'):
                            idx = buf.find(sep)
                            if idx == -1:
                                continue
                            line = buf[:idx].decode(errors='replace').strip()
                            buf = buf[idx + 1:]
                            if not line:
                                continue
                            stderr_lines.append(line)
                            if len(stderr_lines) > 30:
                                stderr_lines.pop(0)
                            if 'frame=' in line:
                                try:
                                    part = line.split('frame=')[1].split()[0]
                                    self._frames_written = int(part)
                                except (IndexError, ValueError):
                                    pass
                            elif 'Opening' in line and '.mp4' in line:
                                # ffmpeg segment log: [segment @ 0x..] Opening 'path/to/x.mp4' for writing
                                m = re.search(r"Opening '([^']+\.mp4)'", line)
                                if m:
                                    self._current_segment_file = m.group(1)
                                logger.info('Recorder %s: %s', self.name, line)
                            break

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    **_popen_kwargs(),
                )
                self._proc = proc
                logger.info('Recorder %s: ffmpeg pid=%d', self.name, proc.pid)

                stderr_thread = threading.Thread(
                    target=_read_stderr, args=(proc.stderr,), daemon=True,
                )
                stderr_thread.start()

                while self._running:
                    try:
                        proc.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        continue

                if not self._running:
                    # External stop() handles SIGINT/SIGTERM/SIGKILL escalation
                    stderr_thread.join(timeout=10)
                    break

                # ffmpeg exited on its own — RTSP disconnect, decode error, etc.
                stderr_thread.join(timeout=5)
                ret = proc.returncode
                if ret != 0:
                    snippet = '\n'.join(stderr_lines[-15:]).strip()
                    logger.warning(
                        'Recorder %s: ffmpeg exited %d (will retry in %ds)\n%s',
                        self.name, ret, reconnect_delay, snippet,
                    )
                    self._status = 'error'
                    self._error = f'ffmpeg exited {ret}'
                else:
                    logger.info('Recorder %s: ffmpeg exited cleanly (will restart)',
                                self.name)

                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 20)

            except FileNotFoundError:
                self._status = 'error'
                self._error = 'ffmpeg binary not found on PATH'
                logger.error('Recorder %s: ffmpeg binary not found', self.name)
                time.sleep(10)
            except Exception as exc:
                self._status = 'error'
                self._error = str(exc)
                logger.exception('Recorder %s: ffmpeg launch failed', self.name)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 20)
            finally:
                self._proc = None

    # ------------------------------------------------------------------ #
    # OpenCV fallback backend
    # ------------------------------------------------------------------ #

    def _record_loop_opencv(self):
        reconnect_delay = 2
        while self._running:
            cap = cv2.VideoCapture(self.rtsp_uri, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                self._status = 'error'
                self._error = f'Cannot connect to {self.rtsp_uri}'
                logger.warning('Recorder %s: %s', self.name, self._error)
                cap.release()
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue

            reconnect_delay = 2
            self._status = 'recording'
            self._error = None

            fps = self.config.VIDEO_FPS
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720

            try:
                while self._running:
                    writer, filepath = self._create_opencv_writer(width, height, fps)
                    self._current_file = filepath
                    segment_start = time.time()
                    segment_frames = 0

                    logger.info('Recorder %s: writing to %s', self.name, filepath)

                    while self._running:
                        ret, frame = cap.read()
                        if not ret:
                            logger.warning(
                                'Recorder %s: lost stream, reconnecting...', self.name,
                            )
                            break
                        writer.write(frame)
                        self._frames_written += 1
                        segment_frames += 1
                        if time.time() - segment_start >= self.config.SEGMENT_DURATION:
                            break

                    writer.release()
                    logger.info(
                        'Recorder %s: segment done, %d frames', self.name, segment_frames,
                    )
                    # Post-process: remux to faststart so browsers can stream it
                    self._remux_faststart(filepath)

                    if not ret:
                        break
            except Exception:
                self._status = 'error'
                logger.exception('Recorder %s: error', self.name)
            finally:
                cap.release()

            if self._running:
                time.sleep(reconnect_delay)

    def _create_opencv_writer(self, width, height, fps):
        filepath = self._next_filepath()
        for codec in (self.config.VIDEO_CODEC, 'avc1', 'H264', 'mp4v'):
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))
            if writer.isOpened():
                logger.info('Recorder %s: codec %s', self.name, codec)
                return writer, filepath
            writer.release()
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))
        return writer, filepath

    def _remux_faststart(self, filepath):
        """Remux finished segment to move moov atom to start (faststart).

        Required for HTTP progressive download in Firefox. No-op if ffmpeg
        is unavailable or the remux fails.
        """
        if not shutil.which('ffmpeg'):
            return
        tmp = filepath + '.faststart.mp4'
        try:
            result = subprocess.run(
                [
                    'ffmpeg', '-y', '-i', filepath,
                    '-c', 'copy', '-movflags', '+faststart',
                    tmp,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
            if result.returncode == 0:
                os.replace(tmp, filepath)
                logger.debug('Recorder %s: remuxed faststart %s', self.name, filepath)
        except Exception:
            logger.debug('Recorder %s: faststart remux skipped', self.name)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _next_filepath(self):
        """OpenCV fallback uses one filepath per segment (loop-driven)."""
        cam_dir = os.path.join(self.config.RECORDINGS_DIR, self.slug)
        os.makedirs(cam_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(cam_dir, f'{self.slug}_{timestamp}.mp4')

    def _segment_pattern(self):
        """ffmpeg segment muxer takes a strftime pattern; ffmpeg expands it
        each time it opens a new segment. Returns (cam_dir, pattern)."""
        cam_dir = os.path.join(self.config.RECORDINGS_DIR, self.slug)
        os.makedirs(cam_dir, exist_ok=True)
        pattern = os.path.join(cam_dir, f'{self.slug}_%Y%m%d_%H%M%S.mp4')
        return cam_dir, pattern


class RecordingManager:
    """Manages multiple camera recorders with retention policy.

    Retention is enforced two ways:
      1. Age-based: files older than RETENTION_DAYS are deleted
      2. Size-based: oldest files deleted when total exceeds MAX_STORAGE_GB

    Retention settings can be changed at runtime via the web UI and are
    persisted to retention.json.
    """

    # Optional metadata fields persisted to cameras.json alongside slug/name/rtsp_uri.
    # Recorder doesn't use these; dashboard's discover endpoint pulls them in.
    _META_FIELDS = (
        'ip_address', 'port', 'manufacturer', 'model',
        'location_name', 'latitude', 'longitude',
        'onvif_username', 'onvif_password',
    )

    def __init__(self, config):
        self.config = config
        self._recorders = {}
        self._metadata = {}  # slug -> dict of optional metadata fields
        self._lock = threading.Lock()
        self._cleanup_thread = None
        self._running = False
        self._retention = self._load_retention()
        # Per-name timestamp of last stop. add_camera waits briefly after a
        # recent stop so the camera's RTSP server has time to free the prior
        # session — many CCTVs reject a second connect within ~1s.
        self._recently_stopped = {}
        # Optional callback (e.g. live stream service) to notify on stop
        self._on_remove = None

    @property
    def retention_days(self):
        return self._retention.get('retention_days', self.config.RETENTION_DAYS)

    @property
    def max_storage_gb(self):
        return self._retention.get('max_storage_gb', self.config.MAX_STORAGE_GB)

    @property
    def cleanup_interval(self):
        return self._retention.get('cleanup_interval', self.config.CLEANUP_INTERVAL)

    def get_retention_settings(self):
        return {
            'retention_days': self.retention_days,
            'max_storage_gb': self.max_storage_gb,
            'cleanup_interval': self.cleanup_interval,
        }

    def update_retention(self, retention_days=None, max_storage_gb=None,
                         cleanup_interval=None):
        if retention_days is not None:
            self._retention['retention_days'] = max(0, int(retention_days))
        if max_storage_gb is not None:
            self._retention['max_storage_gb'] = max(0, int(max_storage_gb))
        if cleanup_interval is not None:
            self._retention['cleanup_interval'] = max(60, int(cleanup_interval))
        self._save_retention()
        logger.info(
            'Retention updated: %d days, %d GB max, %ds interval',
            self.retention_days, self.max_storage_gb, self.cleanup_interval,
        )

    def start(self):
        self._running = True
        cameras = self._load_cameras()
        if cameras:
            logger.info('Resuming %d camera recorder(s) from cameras.json', len(cameras))
        for cam in cameras:
            try:
                display_name = cam.get('name')
                rtsp_uri = cam.get('rtsp_uri')
                if not display_name or not rtsp_uri:
                    logger.warning('Skipping malformed entry: %s', cam)
                    continue
                meta = {k: cam[k] for k in self._META_FIELDS if k in cam}
                self.add_camera(display_name, rtsp_uri, metadata=meta or None)
            except Exception:
                logger.exception('Failed to resume camera %s', cam)
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True,
        )
        self._cleanup_thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            for rec in self._recorders.values():
                rec.stop()

    def set_remove_callback(self, fn):
        """fn(name) is invoked after a recorder is fully stopped. Used by
        the live stream service to release its parallel RTSP session."""
        self._on_remove = fn

    def _wait_cooldown(self, name):
        """Sleep until at least COOLDOWN_SEC have passed since the last stop
        of this name, so the camera's RTSP socket is fully released."""
        COOLDOWN_SEC = 2.0
        ts = self._recently_stopped.get(name)
        if not ts:
            return
        elapsed = time.time() - ts
        if elapsed < COOLDOWN_SEC:
            wait = COOLDOWN_SEC - elapsed
            logger.info('Cooldown %s: sleeping %.1fs before re-add', name, wait)
            time.sleep(wait)

    def add_camera(self, display_name, rtsp_uri, metadata=None):
        """Register a camera under its slug. display_name is preserved for UI.
        metadata is an optional dict of fields (ip_address, model, etc.) that
        the dashboard's discover endpoint can consume."""
        slug = slugify(display_name)
        # Stop any existing recorder for this slug OUTSIDE the lock so other
        # API calls aren't blocked for the full ~10s ffmpeg shutdown window.
        existing = None
        with self._lock:
            if slug in self._recorders:
                existing = self._recorders.pop(slug)
        if existing:
            logger.info('Replacing recorder %s — stopping old instance', slug)
            existing.stop()
            self._recently_stopped[slug] = time.time()
            if self._on_remove:
                try:
                    self._on_remove(slug)
                except Exception:
                    logger.exception('on_remove callback error for %s', slug)

        self._wait_cooldown(slug)

        with self._lock:
            rec = CameraRecorder(slug, display_name, rtsp_uri, self.config)
            self._recorders[slug] = rec
            if metadata:
                clean = {k: v for k, v in metadata.items()
                         if k in self._META_FIELDS and v not in (None, '')}
                if clean:
                    self._metadata[slug] = clean
            rec.start()
        logger.info('Recorder %s started (rtsp=%s, display=%r)', slug, rtsp_uri, display_name)
        self._save_cameras()
        return slug

    def remove_camera(self, slug):
        """Look up by slug. Accepts a display name too — slugifies it first
        so the dashboard can keep calling DELETE with the user-facing name."""
        slug = slugify(slug) if slug else slug
        with self._lock:
            rec = self._recorders.pop(slug, None)
            self._metadata.pop(slug, None)
        if rec:
            logger.info('Removing recorder %s', slug)
            rec.stop()
            self._recently_stopped[slug] = time.time()
            if self._on_remove:
                try:
                    self._on_remove(slug)
                except Exception:
                    logger.exception('on_remove callback error for %s', slug)
        self._save_cameras()

    def get_status(self):
        with self._lock:
            result = {}
            for slug, rec in self._recorders.items():
                result[slug] = {
                    'slug': slug,
                    'name': rec.display_name,
                    'rtsp_uri': rec.rtsp_uri,
                    'status': rec.status,
                    'error': rec.error,
                    'current_file': rec.current_file,
                    'frames_written': rec.frames_written,
                    'last_stderr': rec.last_stderr,
                }
            return result

    def get_camera_list(self):
        with self._lock:
            out = []
            for slug, rec in self._recorders.items():
                entry = {
                    'slug': slug,
                    'name': rec.display_name,
                    'rtsp_uri': rec.rtsp_uri,
                }
                meta = self._metadata.get(slug)
                if meta:
                    entry.update(meta)
                out.append(entry)
            return out

    def get_recorder(self, slug):
        with self._lock:
            return self._recorders.get(slug)

    def in_progress_filename(self, slug):
        """Basename of the segment currently being written by the recorder
        for `slug`, or None. list_recordings filters this out so playback
        only ever shows finalised files."""
        with self._lock:
            rec = self._recorders.get(slug)
        return rec.current_segment_file if rec else None

    def get_recordings_info(self):
        rec_dir = self.config.RECORDINGS_DIR
        if not os.path.exists(rec_dir):
            return {
                'total_files': 0, 'total_size_gb': 0, 'cameras': {},
                'oldest_file': None, 'newest_file': None,
            }

        cameras = {}
        total_size = 0
        total_files = 0
        oldest_time = None
        newest_time = None

        for cam_name in os.listdir(rec_dir):
            cam_dir = os.path.join(rec_dir, cam_name)
            if not os.path.isdir(cam_dir):
                continue
            files = []
            cam_size = 0
            for f in sorted(os.listdir(cam_dir)):
                fpath = os.path.join(cam_dir, f)
                if os.path.isfile(fpath):
                    sz = os.path.getsize(fpath)
                    mtime = os.path.getmtime(fpath)
                    cam_size += sz
                    total_size += sz
                    total_files += 1
                    if oldest_time is None or mtime < oldest_time:
                        oldest_time = mtime
                    if newest_time is None or mtime > newest_time:
                        newest_time = mtime
                    files.append({
                        'name': f,
                        'size_mb': round(sz / 1e6, 1),
                        'modified': datetime.fromtimestamp(mtime).isoformat(),
                    })
            cameras[cam_name] = {
                'files': files[-20:],
                'total_files': len(files),
                'size_gb': round(cam_size / 1e9, 2),
            }

        return {
            'total_files': total_files,
            'total_size_gb': round(total_size / 1e9, 2),
            'cameras': cameras,
            'oldest_file': datetime.fromtimestamp(oldest_time).isoformat() if oldest_time else None,
            'newest_file': datetime.fromtimestamp(newest_time).isoformat() if newest_time else None,
        }

    # --- Persistence ---

    def _load_cameras(self):
        path = self.config.CAMERAS_FILE
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.warning('cameras.json malformed (not list), ignoring')
                return []
            # Normalise legacy entries that lack 'slug' or have different keys.
            out = []
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                rtsp = entry.get('rtsp_uri')
                name = entry.get('name') or entry.get('slug')
                if not rtsp or not name:
                    continue
                normal = {'name': name, 'rtsp_uri': rtsp}
                # Preserve any optional metadata fields
                for k in self._META_FIELDS:
                    if entry.get(k) not in (None, ''):
                        normal[k] = entry[k]
                out.append(normal)
            return out
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning('cameras.json load failed (%s) — starting empty', exc)
            return []

    def _save_cameras(self):
        # Atomic: write to .tmp then rename. Crash mid-write leaves old file intact.
        cameras = self.get_camera_list()
        path = self.config.CAMERAS_FILE
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(cameras, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            logger.error('cameras.json save failed: %s', exc)
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _load_retention(self):
        path = self.config.RETENTION_FILE
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_retention(self):
        path = self.config.RETENTION_FILE
        with open(path, 'w') as f:
            json.dump(self._retention, f, indent=2)

    # --- Retention / Cleanup ---

    def _cleanup_loop(self):
        while self._running:
            try:
                deleted_age = self._cleanup_by_age()
                deleted_size = self._cleanup_by_size()
                if deleted_age or deleted_size:
                    logger.info(
                        'Cleanup: removed %d by age, %d by size',
                        deleted_age, deleted_size,
                    )
            except Exception:
                logger.exception('Cleanup error')
            time.sleep(self.cleanup_interval)

    def _get_all_recording_files(self):
        rec_dir = self.config.RECORDINGS_DIR
        if not os.path.exists(rec_dir):
            return []
        all_files = []
        for root, dirs, files in os.walk(rec_dir):
            for f in files:
                fpath = os.path.join(root, f)
                try:
                    stat = os.stat(fpath)
                    all_files.append((fpath, stat.st_mtime, stat.st_size))
                except OSError:
                    continue
        all_files.sort(key=lambda x: x[1])
        return all_files

    def _cleanup_by_age(self):
        days = self.retention_days
        if days <= 0:
            return 0
        cutoff = time.time() - (days * 86400)
        deleted = 0
        for fpath, mtime, _ in self._get_all_recording_files():
            if mtime < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                except OSError:
                    pass
            else:
                break
        self._cleanup_empty_dirs()
        return deleted

    def _cleanup_by_size(self):
        limit_gb = self.max_storage_gb
        if limit_gb <= 0:
            return 0
        limit_bytes = limit_gb * 1e9
        all_files = self._get_all_recording_files()
        total_bytes = sum(f[2] for f in all_files)
        deleted = 0
        while total_bytes > limit_bytes and all_files:
            fpath, _, fsize = all_files.pop(0)
            try:
                os.remove(fpath)
                total_bytes -= fsize
                deleted += 1
            except OSError:
                pass
        if deleted:
            self._cleanup_empty_dirs()
        return deleted

    def _cleanup_empty_dirs(self):
        rec_dir = self.config.RECORDINGS_DIR
        if not os.path.exists(rec_dir):
            return
        for name in os.listdir(rec_dir):
            cam_dir = os.path.join(rec_dir, name)
            if os.path.isdir(cam_dir) and not os.listdir(cam_dir):
                try:
                    os.rmdir(cam_dir)
                except OSError:
                    pass
