"""CenterFace inference adapted from Star-Clouds/CenterFace (MIT)."""
import cv2
import numpy as np


class CenterFace:
    def __init__(self, model):
        self.net = cv2.dnn.readNetFromONNX(str(model))

    def locate(self, frame, limit=640):
        """Return boxes and five landmarks in YuNet's coordinate format."""
        height, width = frame.shape[:2]
        factor = min(1., limit / max(height, width))
        rh, rw = np.maximum(32, np.ceil(np.array([height, width]) * factor / 32).astype(int) * 32)
        self.net.setInput(cv2.dnn.blobFromImage(frame, size=(int(rw), int(rh)), swapRB=True))
        heatmap, scale, offset, landmarks = self.net.forward(("537", "538", "539", "540"))
        confidence = heatmap[0, 0]
        y, x = np.where(confidence >= .5)
        if not len(x):
            return []
        scores = confidence[y, x]
        extent = np.exp(scale[0, :, y, x][:, ::-1]) * 4
        centers = (np.column_stack((x, y)) + offset[0, :, y, x][:, ::-1] + .5) * 4
        origin = centers - extent / 2
        points = landmarks[0, :, y, x].reshape(-1, 5, 2)[:, :, ::-1] * extent[:, None, :] + origin[:, None, :]
        boxes = np.column_stack((origin, extent))
        picked = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), .5, .3, top_k=100)
        stretch = np.float32([width / rw, height / rh])
        faces = np.column_stack((boxes * np.tile(stretch, 2), (points * stretch).reshape(-1, 10), scores))
        return faces[np.asarray(picked[:25], dtype=np.intp)].astype(np.float32)
