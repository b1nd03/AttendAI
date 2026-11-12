"""Download from pinned upstream commits and verify OpenCV LFS object hashes."""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZOO = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
MINI = "b6d5f04ad78778917853b25c778acef6d5626d15"
CENTERFACE = "b82ec0c4844e89fd5a0305986aed9bdf33c72585"
AURAFACE = "af6d057c9b0ec4071d4c49c80e3539258798b609"

def file_hash(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()

def fetch(url, dest, expected=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not expected or file_hash(dest)==expected):
        return
    print(f"Downloading {dest.name}...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent":"Presence-Setup/1.0"})
    temp = dest.with_suffix(dest.suffix+".download")
    try:
        with urllib.request.urlopen(req, timeout=120) as response, temp.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if expected and file_hash(temp) != expected:
            raise RuntimeError(f"Checksum mismatch for {dest.name}; download was not installed.")
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)

def main():
    base = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{ZOO}/models"
    fetch(base+"/face_detection_yunet/face_detection_yunet_2023mar.onnx",ROOT/"models/yunet.onnx",
          "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4")
    fetch(base+"/face_recognition_sface/face_recognition_sface_2021dec.onnx",ROOT/"models/sface.onnx",
          "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79")
    fetch(base+"/object_detection_nanodet/object_detection_nanodet_2022nov.onnx",ROOT/"models/nanodet.onnx",
          "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186")
    for model in ["face_detection_yunet","face_recognition_sface","object_detection_nanodet"]:
        fetch(f"https://raw.githubusercontent.com/opencv/opencv_zoo/{ZOO}/models/{model}/LICENSE",
              ROOT/f"models/{model}.LICENSE")
    centerface_base = f"https://raw.githubusercontent.com/Star-Clouds/CenterFace/{CENTERFACE}"
    fetch(centerface_base+"/models/onnx/centerface.onnx", ROOT/"models/centerface.onnx",
          "77e394b51108381b4c4f7b4baf1c64ca9f4aba73e5e803b2636419578913b5fe")
    fetch(centerface_base+"/LICENSE", ROOT/"models/centerface.LICENSE")
    auraface_base = f"https://huggingface.co/fal/AuraFace-v1/resolve/{AURAFACE}"
    fetch(auraface_base+"/glintr100.onnx", ROOT/"models/auraface.onnx",
          "a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60")
    fetch(auraface_base+"/LICENSE.md", ROOT/"models/auraface.LICENSE")
    mini_base = f"https://raw.githubusercontent.com/minivision-ai/Silent-Face-Anti-Spoofing/{MINI}"
    vendor = ROOT/"backend/vendor"
    fetch(mini_base+"/src/model_lib/MiniFASNet.py",vendor/"MiniFASNet.py")
    fetch(mini_base+"/src/generate_patches.py",vendor/"generate_patches.py")
    fetch(mini_base+"/LICENSE",vendor/"LICENSE")
    (vendor/"__init__.py").touch()
    for name, expected in {
        "2.7_80x80_MiniFASNetV2.pth":"a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0",
        "4_0_0_80x80_MiniFASNetV1SE.pth":"84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990",
    }.items():
        fetch(mini_base+"/resources/anti_spoof_models/"+name,ROOT/"models"/name,expected)
    manifest = {p.name: file_hash(p)
                for p in (ROOT/"models").iterdir() if p.suffix in (".pth",".onnx")}
    (ROOT/"models/manifest.json").write_text(json.dumps({"opencv_commit":ZOO,"minifasnet_commit":MINI,"centerface_commit":CENTERFACE,
                                                       "auraface_revision":AURAFACE,
                                                       "sha256":manifest},indent=2))
    print("All models downloaded. Weight checksums verified.")

if __name__ == "__main__":
    main()
