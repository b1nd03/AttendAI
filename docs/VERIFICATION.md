# Verification record

7–8 November 2026, local Windows environment. Automated tests run against isolated temporary databases.

| Check | Result | Scope |
|---|---|---|
| `.venv/Scripts/python -m pytest -q --tb=short` | 88 passed | Native-resolution browser upload limits and analysis, distant-face capture, naming and recognition, review recovery, duplicate-group combination, six capture workers, and attendance deduplication. |
| `npm run test:e2e` | 13 passed | Desktop and 360px Chromium, accessibility, high-detail camera selection and resolution fallback, live capture progress, six-camera monitor, mobile duplicate-group comparison, manual confirmation, and attendance export. |
| `npm run build` | Passed | Strict TypeScript and Vite production build. |
| `npm run format:check` | Passed | Frontend and browser-test formatting. |
| `scripts/check_tokens.py` | Passed | DESIGN.md/CSS color mapping drift check. |
| DESIGN.md official lint | 0 errors | Token schema valid; component-reference advisory warnings remain for documented global semantic tokens. |
| `npm audit --omit=dev` | 0 known vulnerabilities reported | JavaScript production dependency audit only; not a general security certification. |
| Model verification | Passed | All seven installed model-weight hashes match explicit expected SHA-256 values. |

One backend-test dependency emits an upstream AnyIO/Starlette deprecation warning. It does not change test outcomes.

Browser naming tests mock an unknown group and its save response to exercise that UI. Separately, the backend integration test creates an unknown group from repeated injected embeddings, names it through the actual API, and recognizes that identity on a second camera. Real-model smoke checks detect and embed two simultaneous faces. These distinguish deterministic decision testing from actual camera-accuracy evidence.

The browser suite captures a simulated camera preview in `test-results/laptop-camera-live.png`.

The high-detail integration test submits 3840×2160 JPEGs through the browser-camera API. The real detector and recognition models save the distant face for naming and match it on subsequent frames. The same face geometry at a 1280-pixel frame width falls below the source-size quality gate. The test uses composited fixture images. Actual results depend on the webcam's negotiated resolution and the number of face pixels it captures.

The six-source integration test uses simulated capture and injected face observations. It verifies simultaneous previews and analysis, six source-specific sightings, one organization-wide check-in, and continued operation on five feeds when one is stopped.

A separate CPU smoke benchmark ran the real face detector, embedding model, and liveness models on six simulated feeds, each repeating the same 512×512 fixture with one usable face. All six reported current analysis in eight one-second observations after warmup. The reported analysis rate was 1.7–1.9 frames per second per source, with a 1.8 median. The test did not exercise RTSP decoding, browser preview traffic, room-distance mode, a populated roster, or a crowded scene.

The workspace database returned `ok` from SQLite's `PRAGMA quick_check` before and after the local restart. The server reported ready on `127.0.0.1:8000`, and SQLite 3.49.1 switched from WAL to rollback journaling as intended.

## Performance measurements

CPU benchmark on the local Windows host. Each path ran 40 timed iterations per fixture after warmup, with before and after calls interleaved. Times include detection, alignment, embeddings, and liveness for eligible faces. They exclude camera delivery, dashboard polling, and the time needed to gather separate observations for attendance.

| Fixture | Before median / p95 | After median / p95 | Eligible faces before / after |
|---|---:|---:|---:|
| Empty 960×720 frame | 44.6 / 51.0 ms | 34.2 / 39.5 ms | 0 / 0 |
| One face, 960×720 | 86.4 / 96.5 ms | 77.0 / 87.5 ms | 1 / 1 |
| Two faces, 1280×720 | 138.7 / 148.7 ms | 97.3 / 114.3 ms | 2 / 2 |

Chromium Resource Timing measured 435 KB of script and stylesheet transfers on the previous sign-in page and 124 KB after the update. The repeat visit reused those static assets from cache. Local page paint was measured over two navigations per version, which is insufficient to establish a load-time trend. The former preview requested an analyzed image every 650 ms. The laptop preview now plays the local media stream directly.

## Still requires installation-specific validation

No physical IP/USB camera or actual enrollment population was supplied. Therefore camera vendor/codec compatibility, hardware throughput, distance/angle/lighting, actual tracking error rate, liveness false acceptance/rejection, face-recognition FAR/FRR, and operational attendance accuracy have not been established for the user's rooms. The research document provides the calibration and shadow-operation procedure. Do not interpret passing software tests as a claim of zero recognition mistakes.
