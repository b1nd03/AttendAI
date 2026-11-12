# Shadow replay and FaceMoE pilot

This tool replays authorized approach clips in batches of up to six. It builds a separate gallery from the enrollment images you supply and uses the existing SFace matcher with AuraFace fallback when its templates are available. It reports what each walk reached, from detection and tracking through identity, liveness and the configured trigger. Reports use aliases from the manifest. The replay uses a temporary data directory, with its report written to the explicit output path.

The pilot uses the published Swin-B WebFace4M BRIAR checkpoint. The weight file is pinned and verified by SHA-256 before loading with PyTorch’s tensor-only loader. The model card lists Apache-2.0. Confirm the exact checkpoint’s workplace-use terms before using the pilot on operational footage. [Research and model selection](LONG_RANGE_RESEARCH.md) · [Publisher checkpoint](https://huggingface.co/kartiknarayan/FaceMoE/tree/main/swin4m_exp_3_k_2_briar_full)

## Run on your cameras’ recordings

1. Export short original-resolution clips from each entrance camera. Use one authorized approach per clip and keep enrollment photos from a separate session. Include independent enrolled and unenrolled approaches at 5, 10 and 15 metres. Give each case and person a pseudonymous ID.
2. Put the clips and the enrollment images beside a `manifest.json` file. The `person` field is the ground truth and is `null` for an unenrolled person. Mark each clip as `calibration` or `holdout`, with both types of person in each split. Use the same `session` ID for clips from the same recording session, even across cameras, and keep sessions entirely within one split. The loader rejects shared sessions and exact file copies. Use genuinely separate sessions because different encodes of the same walk can produce different file hashes.

```json
{
  "enrollment": {
    "member-01": ["member-01-a.jpg", "member-01-b.jpg", "member-01-c.jpg"],
    "member-02": ["member-02-a.jpg", "member-02-b.jpg", "member-02-c.jpg"]
  },
  "probes": [
    {"id": "pass-01", "camera": "corridor", "distance_m": 5, "person": "member-01", "path": "pass-01.mp4", "split": "calibration", "session": "morning-a"},
    {"id": "pass-02", "camera": "glass-doors", "distance_m": 15, "person": null, "path": "pass-02.mp4", "split": "calibration", "session": "morning-b"},
    {"id": "pass-03", "camera": "corridor", "distance_m": 5, "person": null, "path": "pass-03.mp4", "split": "holdout", "session": "evening-a"},
    {"id": "pass-04", "camera": "glass-doors", "distance_m": 15, "person": "member-02", "path": "pass-04.mp4", "split": "holdout", "session": "evening-b", "roi": [0.25, 0.15, 0.50, 0.75], "check_in_line": {"axis": "x", "position": 0.5, "direction": 1}}
  ]
}
```

An optional normalized `roi` of `[left, top, width, height]` limits detection to the walking lane without resizing the crop. Frames with multiple faces inside it are skipped. `check_in_line` uses a position between `0.1` and `0.9` relative to that analyzed region, or the full frame if there is no ROI. Direction `1` means left to right on the `x` axis or top to bottom on `y`. Direction `-1` reverses it. Omit the line for appearance-triggered check-in.

For a supervised liveness trial, add authorized printed-photo and screen-replay clips with `presentation` set to `photo` or `screen`. Use the alias of the person depicted, or `null` if that person is unenrolled. Ordinary approaches use `live` by default. Photo and screen cases are excluded from model cutoff selection and ordinary identification rates. The separate attack summary shows how many had a frame pass liveness and how many would have passed the full baseline check-in gate. [NIST passive presentation attack evaluation](https://www.nist.gov/publications/face-analysis-technology-evaluation-fate-part-10-performance-passive-software-based)

3. From the repository root, run the CPU replay or the optional FaceMoE comparison. Use a new output filename each time.

```powershell
.\.venv\Scripts\python.exe .\scripts\facemoe_pilot.py .\pilot-input\manifest.json --baseline-only --output .\pilot-input\baseline.json
.\.venv-facemoe\Scripts\python.exe .\scripts\facemoe_pilot.py .\pilot-input\manifest.json --output .\pilot-input\scores.json
```

## Interpreting the result

`baseline_stage` gives the furthest step reached or the main blocker. The report includes the largest detected face, face-quality rejections, ambiguous frames, track fragments, distinct usable frames, identity consensus, liveness, and the first check-in that would qualify in the clip. Summary rows report missed enrolled approaches, wrong names and false identifications for unknown visitors by split, camera and distance. The baseline identity summary measures identity alone. The baseline check-in summary includes liveness and the requested trigger.

The pilot samples near 5 fps and sends up to three sharp, distinct crops from the track with the most distinct usable frames to FaceMoE. `facemoe_rank1` is the closest enrolled alias, even for an unknown visitor. `facemoe_identity` is populated only when both score and runner-up margin pass the replay cutoff. Legacy manifests without splits still show the nearest candidate and baseline diagnostics.

The cutoff uses calibration clips only. It requires zero wrong names among enrolled calibration cases and an observed false identification rate at or below `--target-fpir` (default `0.01`), using a fixed runner-up margin (`--facemoe-margin`, default `0.10`). An **exploratory** result means the observed rate passes while the 95% Wilson upper limit exceeds the target. The holdout flag checks that bound again using unseen clips. Small or correlated samples provide limited evidence. Check wrong names and misses separately even when the bound passes. [NIST one-to-many identification evaluation](https://pages.nist.gov/frvt/html/frte1N.html)

The replay uses the reported AttendAI default quality, voting and liveness settings. It samples recorded frames with the single-face detector path. RTSP latency, live room-distance recovery, cross-camera cooldowns, attendance history and live configuration require a separate field trial. FaceMoE results measure identity and per-track crop inference time. The report supplies no FaceMoE liveness or check-in decision. [Field acceptance plan](LONG_RANGE_RESEARCH.md#field-acceptance-test)

In a November 8 synthetic replay, frames made from a still portrait with varying brightness passed the baseline liveness and check-in gates. The case exercised the recorded-video pipeline with a public fixture. Verify printed photos and screen replays on the actual entrance feeds before relying on automatic check-in there.

## Local verification on 7 November 2026

Six simulated feeds replayed 60 sampled frames from public LFW photographs composited into 1920×1080 video. The enrolled probes were different photos from the enrollment images. The 5, 10 and 15 m labels represent simulated face sizes. The largest detected faces per clip ranged from 108 to 162 source pixels.

| GPU precision | Median per three-crop track | Peak CUDA tensor allocation | Correct top-ranked enrolled approaches |
|---|---:|---:|---:|
| FP32 | 82.6 ms | 1,818.6 MiB | 3 of 3 |
| FP16 | 161.6 ms | 925.9 MiB | 3 of 3 |

Both precisions produced the same top candidate on all six clips, with a maximum cosine difference of 0.00028. The existing matcher found the three enrolled identities, rejected the three unenrolled approaches, and withheld liveness verification on this fixture set. The benchmark measured replay on an RTX 3050 Ti. Establish recognition accuracy and throughput using recordings from the deployment site.
