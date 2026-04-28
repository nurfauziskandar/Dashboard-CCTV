import logging
import threading
import time

import cv2

logger = logging.getLogger(__name__)

# Defaults — overridable via Flask config (STREAM_FPS, STREAM_MAX_WIDTH, STREAM_JPEG_QUALITY)
DEFAULT_TARGET_FPS = 10
DEFAULT_MAX_WIDTH = 1280
DEFAULT_JPEG_QUALITY = 65


class StreamService:
    """RTSP -> MJPEG proxy.

    Capture thread reads raw frames from RTSP and stores the latest one
    in a buffer. JPEG encoding happens on consumer demand, rate-limited
    to a target FPS and downscaled to a max width. This keeps the CPU
    bound to consumer rate (~10 enc/s) instead of native stream rate
    (often 25-30/s, sometimes higher).
    """

    def __init__(self, target_fps=None, max_width=None, jpeg_quality=None):
        self._streams = {}
        self._lock = threading.Lock()
        self.target_fps = target_fps or DEFAULT_TARGET_FPS
        self.max_width = max_width or DEFAULT_MAX_WIDTH
        self.jpeg_quality = jpeg_quality or DEFAULT_JPEG_QUALITY

    def _get_or_create(self, camera):
        key = f'cam_{camera.id}'
        with self._lock:
            cap = self._streams.get(key)
            if cap is None or not cap.alive:
                if cap is not None:
                    cap.stop()
                cap = _RTSPCapture(camera.stream_uri)
                cap.start()
                self._streams[key] = cap
            cap.acquire()
            return cap

    def _release(self, camera):
        key = f'cam_{camera.id}'
        with self._lock:
            cap = self._streams.get(key)
            if cap is None:
                return
            if cap.release_ref() <= 0:
                self._streams.pop(key, None)
                cap.stop()

    def get_frame_generator(self, camera):
        """Yield MJPEG multipart frames at target_fps."""
        if not camera.stream_uri:
            return

        capture = self._get_or_create(camera)
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
                    # No frame yet — wait a bit, don't burn CPU
                    time.sleep(0.2)
                    continue
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n'
                )
        except (GeneratorExit, ConnectionError):
            pass
        finally:
            self._release(camera)

    def get_snapshot(self, camera):
        """Single JPEG. Reuses live capture if active, else opens one-shot."""
        if not camera.stream_uri:
            return None
        with self._lock:
            cap = self._streams.get(f'cam_{camera.id}')
        if cap is not None and cap.alive:
            return cap.encode_jpeg(self.max_width, 80)

        c = cv2.VideoCapture(camera.stream_uri, cv2.CAP_FFMPEG)
        c.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        try:
            if c.isOpened():
                ret, frame = c.read()
                if ret:
                    frame = _downscale(frame, self.max_width)
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    return buf.tobytes()
        finally:
            c.release()
        return None

    def release(self, camera_id):
        key = f'cam_{camera_id}'
        with self._lock:
            cap = self._streams.pop(key, None)
        if cap:
            cap.stop()

    def release_all(self):
        with self._lock:
            keys = list(self._streams.keys())
        for k in keys:
            cap = self._streams.pop(k, None)
            if cap:
                cap.stop()


def _downscale(frame, max_width):
    if frame is None:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / float(w)
    return cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)


class _RTSPCapture:
    """Background RTSP reader. Stores latest raw frame only — no encoding."""

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

    def get_raw_frame(self):
        with self._frame_lock:
            return self._frame  # numpy array — caller treats as read-only

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
        # Force TCP transport (UDP loses packets badly on Wi-Fi) and a
        # 5s socket timeout. CAP_PROP_OPEN_TIMEOUT_MSEC alone is unreliable.
        uri = self.uri
        if uri.startswith('rtsp://') and 'timeout' not in uri:
            sep = '&' if '?' in uri else '?'
            uri += f'{sep}timeout=5000000'

        # Hint OpenCV/ffmpeg to use TCP — works for most installs
        import os
        os.environ.setdefault(
            'OPENCV_FFMPEG_CAPTURE_OPTIONS',
            'rtsp_transport;tcp',
        )

        reconnect_delay = 1
        while self._running:
            try:
                self._cap = cv2.VideoCapture(uri, cv2.CAP_FFMPEG)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)

                if not self._cap.isOpened():
                    logger.warning('RTSP open failed: %s', self.uri)
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
                            logger.warning('RTSP read failed 5x, reconnecting: %s', self.uri)
                            break
                        time.sleep(0.2)
                        continue
                    fail_count = 0
                    with self._frame_lock:
                        self._frame = frame

            except Exception:
                logger.exception('RTSP capture loop error')
            finally:
                if self._cap:
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None

            if self._running:
                time.sleep(reconnect_delay)
