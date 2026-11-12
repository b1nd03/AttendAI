# Third-party artifacts

- OpenCV Zoo YuNet model: MIT notice in `models/face_detection_yunet.LICENSE`.
- OpenCV Zoo SFace model: Apache-2.0 notice in `models/face_recognition_sface.LICENSE`.
- OpenCV Zoo NanoDet-M-Plus person detection model and reference decoder: Apache-2.0 notice in `models/object_detection_nanodet.LICENSE`. AttendAI's decoder is adapted to filter the person class before decoding and combine overlapping camera views.
- Star-Clouds CenterFace model and reference decoder: MIT notice in `models/centerface.LICENSE`. AttendAI's decoder adapts boxes and landmarks to the existing face pipeline.
- fal AuraFace v1 recognition model: Apache-2.0 notice in `models/auraface.LICENSE`. Only the recognition head is installed. The model revision and weight checksum are pinned in the setup script.
- MiniFASNet definitions, crop helper, and checkpoints: Minivision Silent-Face-Anti-Spoofing, Apache-2.0, pinned commit in the model download script. Source definitions are unmodified in `backend/vendor`; license is `backend/vendor/LICENSE`.
- IBM Plex Sans and IBM Plex Mono: SIL Open Font License, self-hosted via `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono`. The packages retain their licenses.
- React, Radix Dialog, Lucide, Vite and Python runtime dependencies retain their package licenses.
- The optional test fixture script obtains OpenCV's `samples/data/lena.jpg` from the upstream repository for detector and integration tests.

Downloaded model byte hashes are in `models/manifest.json`. No InsightFace pretrained model pack or scraped face training dataset is bundled.
