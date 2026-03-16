import io
import time
import math
import random
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


class FakeStreamService:
    """Generate synthetic MJPEG frames for demo mode.

    Produces animated test-pattern frames with camera name overlay,
    timestamp, and simulated motion to mimic a live CCTV feed.
    """

    def get_frame_generator(self, camera):
        """Yield JPEG frames as a multipart MJPEG stream."""
        frame_num = 0
        while True:
            frame = self._generate_frame(camera, frame_num)
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
            )
            frame_num += 1
            time.sleep(0.1)  # ~10 FPS

    def get_snapshot(self, camera):
        """Return a single JPEG snapshot frame."""
        return self._generate_frame(camera, 0)

    def release(self, camera_id):
        pass

    def release_all(self):
        pass

    def _generate_frame(self, camera, frame_num):
        """Create a synthetic surveillance-style frame."""
        w, h = 640, 360

        # Dark background with subtle animated gradient
        t = frame_num * 0.05
        bg_r = int(20 + 5 * math.sin(t))
        bg_g = int(22 + 5 * math.sin(t + 1))
        bg_b = int(28 + 5 * math.sin(t + 2))
        img = Image.new('RGB', (w, h), (bg_r, bg_g, bg_b))
        draw = ImageDraw.Draw(img)

        # Grid lines (surveillance look)
        grid_color = (40, 45, 55)
        for x in range(0, w, 40):
            draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
        for y in range(0, h, 40):
            draw.line([(0, y), (w, y)], fill=grid_color, width=1)

        # Simulated moving objects (boxes)
        random.seed(camera.id if camera else 1)
        obj_count = random.randint(2, 4)
        for i in range(obj_count):
            base_x = random.randint(50, w - 100)
            base_y = random.randint(80, h - 100)
            speed_x = random.uniform(0.5, 2.0) * (1 if random.random() > 0.5 else -1)
            speed_y = random.uniform(0.3, 1.0) * (1 if random.random() > 0.5 else -1)

            ox = int((base_x + frame_num * speed_x) % (w - 60))
            oy = int((base_y + frame_num * speed_y) % (h - 60))
            obj_w = random.randint(20, 50)
            obj_h = random.randint(30, 60)

            # Object silhouette
            obj_color = (
                60 + random.randint(0, 30),
                65 + random.randint(0, 30),
                75 + random.randint(0, 30),
            )
            draw.rectangle([ox, oy, ox + obj_w, oy + obj_h], fill=obj_color)

        # Scanline effect
        if frame_num % 3 == 0:
            scan_y = (frame_num * 4) % h
            draw.line([(0, scan_y), (w, scan_y)], fill=(50, 55, 65), width=1)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        except (IOError, OSError):
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
                font_sm = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 11)
            except (IOError, OSError):
                font = ImageFont.load_default()
                font_sm = font

        # Top-left: camera name
        name = camera.name if camera else "CAMERA"
        draw.rectangle([0, 0, w, 24], fill=(0, 0, 0, 180))
        draw.text((8, 4), name.upper(), fill=(0, 200, 80), font=font)

        # Top-right: REC indicator (blinking)
        if frame_num % 20 < 14:
            rec_x = w - 60
            draw.ellipse([rec_x, 6, rec_x + 10, 16], fill=(220, 40, 40))
            draw.text((rec_x + 14, 4), "REC", fill=(220, 40, 40), font=font_sm)

        # Bottom-left: timestamp
        now = datetime.now()
        ts = now.strftime("%d/%m/%Y  %H:%M:%S")
        draw.rectangle([0, h - 22, w, h], fill=(0, 0, 0, 180))
        draw.text((8, h - 18), ts, fill=(180, 190, 200), font=font_sm)

        # Bottom-right: DEMO label
        draw.text((w - 55, h - 18), "DEMO", fill=(100, 110, 120), font=font_sm)

        # Encode to JPEG
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=75)
        return buf.getvalue()
