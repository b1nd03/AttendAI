"""Prepare the OpenCV detector fixture and its enrollment variants."""

import hashlib
from pathlib import Path
from urllib.request import urlopen

import cv2


URL = 'https://raw.githubusercontent.com/opencv/opencv/4.12.0/samples/data/lena.jpg'
SHA256 = '7de7ed51a1594fff247f4cae2301eceacf5313d6011e37b4a4c8733f7bb72c07'


def main():
    directory = Path(__file__).parent / 'fixtures'
    directory.mkdir(exist_ok=True)
    face = directory / 'face.jpg'
    if face.exists():
        source = face.read_bytes()
    else:
        with urlopen(URL, timeout=30) as response:
            source = response.read()
    if hashlib.sha256(source).hexdigest() != SHA256:
        raise SystemExit('The OpenCV fixture failed its checksum. Check tests/fixtures/face.jpg.')
    if not face.exists():
        face.write_bytes(source)

    image = cv2.imread(str(face))
    if image is None:
        raise SystemExit('The OpenCV fixture could not be decoded.')
    for index, brightness in enumerate((-5, 0, 5)):
        variant = cv2.convertScaleAbs(image, alpha=1, beta=brightness)
        path = directory / f'enroll-{index}.jpg'
        if not cv2.imwrite(str(path), variant):
            raise SystemExit(f'Could not write {path.name}.')


if __name__ == '__main__':
    main()
