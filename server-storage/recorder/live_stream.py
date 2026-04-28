"""Live MJPEG stream service for storage server.

Opens a second RTSP session per camera (alongside the recorder), shared
across all viewers via refcount. Lazy: connection only opens on first
viewer; closes when last one disconnects.

Capture loop stores raw frames; JPEG encoding happens on consumer demand
at LIVE_FPS so CPU is bound to viewer rate, not native stream rate.
"""

import os
import time
import threading
import logging

import cv2

logger = logging.getLogger(__name__)


def _downscale(frame, max_width):
    if frame is None:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / float(w)
    return cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)


class _RTSPCapture:
    """Background RTSP reader. Stores latest raw frame only."""

    def __init__(self, uri):
        self.uri = uri
        self._frame = None
        self._frame_lock = threading.Lock()
        self._running = False
        self._thread = None
        self._cap = None
        self._refcount = 0
        self._refcount_lock = threading.Lock()

    @property
    def alive(self):
        return self._running and (self._thread is not None and self._thread.is_alive())

    def acquire(self):
        with self._refcount_lock:
            self._refcount += 1
            return self._refcount

    def release_ref(self):
        with self._refcount_lock:
            self._refcount -= 1
            return self._refcount

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def encode_jpeg(self, max_width, quality):
        with self._frame_lock:
            frame = self._frame
        if frame is None:
            return None
        frame = _downscale(frame, max_width)
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return None
        return buf.tobytes()

    def _capture_loop(self):
        os.environ.setdefault(
            'OPENCV_FFMPEG_CAPTURE_OPTIONS',
            'rtsp_transport;tcp',
        )

        reconnect_delay = 1
        while self._running:
            try:
                self._cap = cv2.VideoCapture(self.uri, cv2.CAP_FFMPEG)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)

                if not self._cap.isOpened():
                    logger.warning('Live RTSP open failed: %s', self.uri)
                    self._cap.release()
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30)
                    continue

                reconnect_delay = 1
                fail_count = 0
                while self._running:
                    ret, frame = self._cap.read()
                    if not ret:
                        fail_count += 1
                        if fail_count >= 5:
                            logger.warning('Live RTSP read failed 5x: %s', self.uri)
                            break
                        time.sleep(0.2)
                        continue
                    fail_count = 0
                    with self._frame_lock:
                        self._frame = frame
            except Exception:
                logger.exception('Live capture loop error')
            finally:
                if self._cap:
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None

            if self._running:
                time.sleep(reconnect_delay)


class LiveStreamService:

    def __init__(self, config, rec_manager):
        self.config = config
        self.rec_manager = rec_manager
        self._streams = {}
        self._lock = threading.Lock()
        self.target_fps = config.LIVE_FPS
        self.max_width = config.LIVE_MAX_WIDTH
        self.jpeg_quality = config.LIVE_JPEG_QUALITY

    def _get_rtsp_uri(self, name):
        for cam in self.rec_manager.get_camera_list():
            if cam['name'] == name:
                return cam['rtsp_uri']
        return None

    def _get_or_create(self, name):
        with self._lock:
            cap = self._streams.get(name)
            if cap is None or not cap.alive:
                if cap is not None:
                    cap.stop()
                uri = self._get_rtsp_uri(name)
                if not uri:
                    return None
                cap = _RTSPCapture(uri)
                cap.start()
                self._streams[name] = cap
            cap.acquire()
            return cap

    def _release(self, name):
        with self._lock:
            cap = self._streams.get(name)
            if cap is None:
                return
            if cap.release_ref() <= 0:
                self._streams.pop(name, None)
                cap.stop()

    def get_frame_generator(self, name):
        capture = self._get_or_create(name)
        if capture is None:
            return
        interval = 1.0 / max(self.target_fps, 1)
        last = 0.0
        try:
            while True:
                now = time.monotonic()
                wait = interval - (now - last)
                if wait > 0:
                    time.sleep(wait)
                last = time.monotonic()
                jpeg = capture.encode_jpeg(self.max_width, self.jpeg_quality)
                if jpeg is None:
                    time.sleep(0.2)
                    continue
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n'
                )
        except (GeneratorExit, ConnectionError):
            pass
        finally:
            self._release(name)

    def stop_one(self, name):
        with self._lock:
            cap = self._streams.pop(name, None)
        if cap:
            cap.stop()

    def stop_all(self):
        with self._lock:
            keys = list(self._streams.keys())
        for k in keys:
            cap = self._streams.pop(k, None)
            if cap:
                cap.stop()
