import time
from datetime import datetime
from picamera2 import Picamera2


class Camera:
    def __init__(self, width: int = 720, height: int = 480):
        self._width = width
        self._height = height
        self._cam = Picamera2()
        config = self._cam.create_preview_configuration(
            main={"size": (width, height), "format": "BGR888"},
        )
        self._cam.configure(config)
        self._cam.start()
        # Let the camera settle
        time.sleep(0.5)

    def take_picture(self, save_path: str | None = None):
        frame = self._cam.capture_array()
        if save_path is not None:
            import cv2
            cv2.imwrite(save_path, frame)
        return frame

    def stop(self):
        self._cam.stop()

    def close(self):
        self._cam.close()


if __name__ == "__main__":
    import os

    cam = Camera()
    try:
        out_dir = os.path.join(os.path.dirname(__file__), "..", "img")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"pic_{stamp}.jpg")
        cam.take_picture(save_path=path)
        print(f"Saved → {path}")
    finally:
        cam.stop()
        cam.close()
