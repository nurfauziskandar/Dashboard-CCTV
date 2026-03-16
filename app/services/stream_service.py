import threading
import time
import cv2


class StreamService:
    """RTSP to MJPEG proxy service.

    Captures RTSP frames using OpenCV and serves them as MJPEG
    multipart stream for browser consumption.
    """

    def __init__(self):
        self._streams = {}
        self._lock = threading.Lock()

    def get_frame_generator(self, camera):
        """Return a generator that yields JPEG frames from the camera's RTSP stream."""
        stream_uri = camera.stream_uri
        if not stream_uri:
            return

        key = f"cam_{camera.id}"

        with self._lock:
            if key not in self._streams:
                self._streams[key] = _RTSPCapture(stream_uri)
                self._streams[key].start()

        capture = self._streams[key]

        try:
            while True:
                frame = capture.get_frame()
                if frame is not None:
                    yield (
                        b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                    )
                else:
                    time.sleep(0.1)
        except GeneratorExit:
            pass

    def get_snapshot(self, camera):
        """Grab a single JPEG frame from the camera."""
        stream_uri = camera.stream_uri
        if not stream_uri:
            return None

        cap = cv2.VideoCapture(stream_uri, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        try:
            if cap.isOpened():
                ret, frame = cap.read()
                if ret:
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    return buf.tobytes()
        finally:
            cap.release()
        return None

    def release(self, camera_id):
        """Stop and release a camera stream."""
        key = f"cam_{camera_id}"
        with self._lock:
            capture = self._streams.pop(key, None)
        if capture:
            capture.stop()

    def release_all(self):
        """Stop all active streams."""
        with self._lock:
            keys = list(self._streams.keys())
        for key in keys:
            capture = self._streams.pop(key, None)
            if capture:
                capture.stop()


class _RTSPCapture:
    """Background thread that continuously reads frames from an RTSP source."""

    def __init__(self, uri):
        self.uri = uri
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._cap = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap:
            self._cap.release()

    def get_frame(self):
        with self._lock:
            return self._frame

    def _capture_loop(self):
        reconnect_delay = 1

        while self._running:
            self._cap = cv2.VideoCapture(self.uri, cv2.CAP_FFMPEG)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)

            if not self._cap.isOpened():
                self._cap.release()
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)
                continue

            reconnect_delay = 1

            while self._running:
                ret, frame = self._cap.read()
                if not ret:
                    break

                _, buf = cv2.imencode(
                    '.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70]
                )
                with self._lock:
                    self._frame = buf.tobytes()

            self._cap.release()

            if self._running:
                time.sleep(reconnect_delay)
