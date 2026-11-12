"""Cached CPU ensemble of the two official MiniFASNet checkpoints.

Upstream preprocessing: BGR uint8 -> CHW float32 without /255, 80px crop,
scales 2.7 and 4.0. Class index 1 = real. This is an uncalibrated PAD score.
"""
import cv2
import numpy as np
import torch

from .vendor.MiniFASNet import MiniFASNetV2, MiniFASNetV1SE
from .vendor.generate_patches import CropImage

class AntiSpoof:
    def __init__(self, directory):
        torch.set_num_threads(2)
        self.models=[]
        for name, factory, scale in [
            ("2.7_80x80_MiniFASNetV2.pth",MiniFASNetV2,2.7),
            ("4_0_0_80x80_MiniFASNetV1SE.pth",MiniFASNetV1SE,4.0),
        ]:
            model=factory(conv6_kernel=(5,5))
            state=torch.load(directory/name,map_location="cpu",weights_only=True)
            state={k.removeprefix("module."):v for k,v in state.items()}
            model.load_state_dict(state)
            model.eval()
            self.models.append((model,scale))
        self.cropper=CropImage()

    def predict(self, frame, box):
        predictions=[]
        with torch.inference_mode():
            for model,scale in self.models:
                crop=self.cropper.crop(frame,box,scale,80,80)
                tensor=torch.from_numpy(np.ascontiguousarray(crop.transpose(2,0,1))).float().unsqueeze(0)
                scores=torch.softmax(model(tensor),dim=1)[0].numpy()
                predictions.append(scores)
        # Require both models to prefer real; use their weaker real score.
        if any(int(np.argmax(p))!=1 for p in predictions):
            return 0.0
        return float(min(p[1] for p in predictions))
