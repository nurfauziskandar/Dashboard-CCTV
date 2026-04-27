import os
import re
import time
import json
import threading
import logging
from datetime import datetime, timedelta

import cv2

logger = logging.getLogger(__name__)

# Characters not allowed in filenames on Windows
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name):
    """Sanitize a string for use as a filename on any OS."""
    return _UNSAFE_CHARS.sub('_', name).strip('. ')


class CameraRecorder:
    """Records a single RTSP camera stream to disk in segmented video files."""

    def __init__(self, name, rtsp_uri, config):
        self.name = name
        self.rtsp_uri = rtsp_uri
        self.config = config
        self._running = False
        self._thread = None
        self._current_file = None
        self._frames_written = 0
        self._status = 'stopped'
        self._error = None
        self._started_at = None

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
        if self._thread:
            self._thread.join(timeout=10)
        self._status = 'stopped'

    def _record_loop(self):
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
                    writer, filepath = self._create_writer(width, height, fps)
                    self._current_file = filepath
                    segment_start = time.time()
                    segment_frames = 0

                    logger.info('Recorder %s: writing to %s', self.name, filepath)

                    while self._running:
                        ret, frame = cap.read()
                        if not ret:
                            logger.warning(
                                'Recorder %s: lost stream, reconnecting...',
                                self.name,
                            )
                            break

                        writer.write(frame)
                        self._frames_written += 1
                        segment_frames += 1

                        elapsed = time.time() - segment_start
                        if elapsed >= self.config.SEGMENT_DURATION:
                            break

                    writer.release()
                    logger.info(
                        'Recorder %s: segment done, %d frames',
                        self.name, segment_frames,
                    )

                    if not ret:
                        break

            except Exception as e:
                self._status = 'error'
                self._error = str(e)
                logger.exception('Recorder %s: error', self.name)
            finally:
                cap.release()

            if self._running:
                time.sleep(reconnect_delay)

    def _create_writer(self, width, height, fps):
        safe_name = _safe_filename(self.name)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{safe_name}_{timestamp}.mp4'

        cam_dir = os.path.join(self.config.RECORDINGS_DIR, safe_name)
        os.makedirs(cam_dir, exist_ok=True)
        filepath = os.path.join(cam_dir, filename)

        # Try H.264 first (browser-compatible), fall back to mp4v if unavailable
        for codec in (self.config.VIDEO_CODEC, 'avc1', 'H264', 'mp4v'):
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))
            if writer.isOpened():
                logger.info('Recorder %s: using codec %s', self.name, codec)
                return writer, filepath
            writer.release()

        # Last resort
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(filepath, fourcc, fps, (width, height))
        return writer, filepath


class RecordingManager:
    """Manages multiple camera recorders with retention policy.

    Retention is enforced two ways:
      1. Age-based: files older than RETENTION_DAYS are deleted
      2. Size-based: oldest files deleted when total exceeds MAX_STORAGE_GB

    Retention settings can be changed at runtime via the web UI and are
    persisted to retention.json.
    """

    def __init__(self, config):
        self.config = config
        self._recorders = {}
        self._lock = threading.Lock()
        self._cleanup_thread = None
        self._running = False
        self._retention = self._load_retention()

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
        """Update retention settings at runtime."""
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
        for cam in cameras:
            self.add_camera(cam['name'], cam['rtsp_uri'])
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True
        )
        self._cleanup_thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            for rec in self._recorders.values():
                rec.stop()

    def add_camera(self, name, rtsp_uri):
        with self._lock:
            if name in self._recorders:
                self._recorders[name].stop()
            rec = CameraRecorder(name, rtsp_uri, self.config)
            self._recorders[name] = rec
            rec.start()
        self._save_cameras()

    def remove_camera(self, name):
        with self._lock:
            rec = self._recorders.pop(name, None)
        if rec:
            rec.stop()
        self._save_cameras()

    def get_status(self):
        with self._lock:
            result = {}
            for name, rec in self._recorders.items():
                result[name] = {
                    'name': name,
                    'rtsp_uri': rec.rtsp_uri,
                    'status': rec.status,
                    'error': rec.error,
                    'current_file': rec.current_file,
                    'frames_written': rec.frames_written,
                }
            return result

    def get_camera_list(self):
        with self._lock:
            return [
                {'name': name, 'rtsp_uri': rec.rtsp_uri}
                for name, rec in self._recorders.items()
            ]

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
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return []

    def _save_cameras(self):
        cameras = self.get_camera_list()
        path = self.config.CAMERAS_FILE
        with open(path, 'w') as f:
            json.dump(cameras, f, indent=2)

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
        """Run retention cleanup periodically."""
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
        """Scan recordings dir, return list of (path, mtime, size) sorted oldest first."""
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

        all_files.sort(key=lambda x: x[1])  # oldest first
        return all_files

    def _cleanup_by_age(self):
        """Delete recordings older than retention_days. Returns count deleted."""
        days = self.retention_days
        if days <= 0:
            return 0

        cutoff = time.time() - (days * 86400)
        deleted = 0

        for fpath, mtime, fsize in self._get_all_recording_files():
            if mtime < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                    logger.debug(
                        'Retention: deleted %s (age: %d days)',
                        fpath,
                        int((time.time() - mtime) / 86400),
                    )
                except OSError:
                    pass
            else:
                break  # sorted by time, no more old files

        # Clean up empty camera directories
        self._cleanup_empty_dirs()
        return deleted

    def _cleanup_by_size(self):
        """Delete oldest recordings when total exceeds max_storage_gb. Returns count deleted."""
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
                logger.debug('Retention: deleted %s (over size limit)', fpath)
            except OSError:
                pass

        if deleted:
            self._cleanup_empty_dirs()
        return deleted

    def _cleanup_empty_dirs(self):
        """Remove empty subdirectories in recordings."""
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
