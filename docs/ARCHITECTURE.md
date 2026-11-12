# Architecture and domain contract

## Continuous path

```text
RTSP / RTSPS / HTTP-MJPEG / USB
    → per-camera capture thread + bounded read timeout
    → single latest-frame slot (old frames replaced)
    → fair shared CPU inference scheduler
    → YuNet multi-face detection
    → per-face size and boundary checks
    → align to 112×112, then check inner-face detail and exposure
    → normalized SFace embedding + MiniFASNet liveness
    → conservative per-camera position + appearance association
    → identity consensus over 5–10 distinct matching frames
    → shared known-face gallery OR unknown face group
    → camera sighting + optional automatic attendance + optional strict template update
```

Server-connected sources continue without an open page. Stopping a camera sets its cancellation signal and clears live previews. FFmpeg source open/read timeouts are four seconds. Reconnect uses 1–30 second backoff. USB tries Windows DirectShow and Media Foundation. Enabled server-connected sources resume on startup. A single backend process owns all capture workers.

Browser camera sharing opens the video device through browser permission and sends bounded JPEG frames to the local server. One frame is in flight at a time. The server authenticates each upload against its owning session and a temporary camera token, then feeds the same latest-frame scheduler used by other sources. The camera lease expires after 15 seconds without frames. Stop, sign-out, and tab close release the browser device and its lease. Browser shares remain off after server restart until an administrator shares again.

Inference instances are shared and locked because OpenCV DNN inputs are mutable. The scheduler skips frames older than 1.5 seconds and replaces obsolete frames instead of queuing them. Live YuNet detection uses an 832-pixel long edge at the default quality floor. Box and landmark coordinates are mapped back to the analysis source for quality checks, SFace alignment, and liveness.

The owning browser plays the laptop camera's original media stream. The camera wall fetches the latest available server frame for other cameras, independent of inference. The optional detection view shows boxes and labels on the frame that produced them. Server preview JPEGs are generated on demand at up to 720 pixels on the long edge, reused by frame sequence, and discarded when a source stops. Hidden and offscreen previews suspend fetching. Snapshot routes reject frames older than four seconds. Each camera exposes its effective analysis rate and the delay from frame receipt to analysis completion.

Room-distance mode is opt-in for server-connected cameras and preserves source pixels up to a 4096-pixel long edge. A shared NanoDet model proposes anonymous bodies from a reduced overview and two overlapping views for wide streams at 1920 pixels or more. Each worker keeps short-lived body regions and follows them on a bounded gray image between detector scans. YuNet examines each head region in source coordinates and still checks the overview for faces. The scheduler deduplicates detections across views before alignment, matching, liveness, and attendance. Person-only regions appear in the detection view and the live count.

Browser sharing preserves up to a 1920-pixel long edge in standard mode. The opt-in high-detail mode requests 3840×2160, accepts up to 9 million pixels and 1 MB per JPEG, and targets three uploads per second. The browser adjusts JPEG quality before reducing frame dimensions to meet the byte limit. The latest-frame scheduler preserves the delivered resolution. When room analysis finds no usable face, a high-detail browser worker can check one overlapping native crop at a time. Measurements and embeddings use source coordinates, and the quality thresholds apply equally to recovered detections.

When a regular scan finds no faces, the camera worker periodically tries one secondary view. The recovery sequence uses CenterFace and rotated YuNet scans, then reuses a view that finds a face. It transforms the landmarks and bounds back into camera coordinates. Clipped faces show a position hint. The recognition and liveness models receive the original camera frame, with source-pixel dimensions enforced before matching.

## Identity and tracking

Each visible face has a temporary camera-local track ID. Matching uses position overlap and normalized embedding similarity; incompatible appearance cannot inherit a prior track's votes. Ambiguous associations start fresh temporal history. Up to two consecutive poor-quality detections can pause evidence collection. Longer runs clear the evidence. Tracks expire after 2.5 seconds without observations.

The shared gallery is indexed in memory from the active SFace templates and invalidated after gallery changes. It still ranks people by the mean of their two best template scores and checks the gap to the next person. Up to two uncertain observations can sit between matching votes. They supply no identity evidence and still contribute to the liveness gate. A conflicting identity clears previous votes.

The live pipeline can try one AuraFace embedding per analyzed frame for a close SFace miss. Both galleries must cover the same active roster, with at least three supervised AuraFace anchors per person. The second gallery independently checks the same leading identity. Recovered votes still go through the existing consensus and liveness gates. They cannot seed automatic template updates. Model-specific templates are encrypted and linked to their enrollment anchors. Refreshing a profile verifies the new images against its current identity and replaces its active templates in one transaction.

No name is inferred from body shape. There is no full-body re-identification or invisible/occluded tracking. Cross-camera identity comes from independently matching the same verified known-person gallery. Unknown clusters may group similar face samples across cameras, but a human must inspect the grouping before assigning a name.

Gallery score is the mean of a person's top two template similarities. Both a threshold and a runner-up identity margin are required. Recognition is open-set: a nearest vector does not force a name. Five to ten successive votes must agree. Repeated identical crops cannot manufacture extra votes from a frozen feed. Similarity scores are not posterior probabilities.

## Learning a named person

Unknown sightings never mark attendance. When collection is enabled, a stable unknown track contributes up to five encrypted crops and embeddings to a face group. Liveness can be unverified at this stage; therefore naming requires human review and in-person verification, just like manual enrollment. Unknown groups are machine suggestions, not asserted identities.

Naming a group requires a unique employee/student ID, a name and group, explicit biometric consent, administrator verification, at least three samples, and no existing gallery identity match. The reviewed templates become fixed enrollment anchors. Past linked sightings are labeled `named` (human-labeled after observation); their attendance remains unchanged. The original unknown samples are deleted after transfer. Subsequent appearances can match the new gallery immediately; no neural network retraining is involved.

Live review starts after three distinct, compatible unknown samples. A group can collect up to five samples while the person stays in view. These observations create a naming task and do not count toward automatic attendance.

Sighting creation and review saving have separate lifecycles. When collection is enabled, an already-visible unknown can enter review after three fresh compatible crops. The camera links the saved group to its existing sighting. A full queue delays the save and retries while suitable samples remain in view. Deleting a group suppresses re-saving it on that continuous track. The operator can capture a new track by stepping out of view for a few seconds and returning.

Automatic grouping checks multiple observations against a group's samples and requires separation from competing groups. An administrator can combine two confirmed groups from Name People. The operation keeps up to five distinct crops across both angles and transfers sighting history. If the selected crops disagree in SFace, all selected crops must agree in AuraFace before the combination can be saved. The naming action rechecks this evidence and records consent. Combining groups alone creates no attendance events.

Automatic updates require all of:

- Automatic learning is enabled.
- A stable verified match, cosine score >= the update threshold (initially .80).
- Identity gap >= max(.15, configured margin).
- Quality >= .80 and weakest liveness vote >= .95.
- Agreement with an original verified enrollment anchor.
- A genuinely varied vector (similarity to existing templates <= .98).
- At most one update per person/hour, and the fixed configured gallery cap (5–10).

At capacity, only an adaptive template can be replaced. Enrollment anchors are never replaced automatically. Additions retain encrypted evidence and an audit record. Rollback disables the new template and restores its displaced template when available. This reduces obvious drift routes; it is not a proof against biometric poisoning.

## Sightings versus attendance

Sightings store source, camera label, location via current source metadata, person/temporary track reference, verification state, score, liveness, attendance effect, and UTC time. A recognized person can have independent sightings on Camera 1 and Camera 2 without receiving duplicate attendance. Repeated same-state sightings on the same camera deduplicate for 60 seconds and once per active track.

Attendance has a global configurable cooldown and suppresses repeated same-direction events on the same local day. Its initial rule is first stable liveness-verified observation. Alternatives include a check-out camera, a sightings-only camera, an entrance-line crossing, or bidirectional entry/exit. The line uses normalized coordinates and a 3% hysteresis band. Crossing uses observed face-center motion and requires testing with the installed camera geometry. Each physical crossing can produce one attendance event. Sighting deduplication runs independently of the attendance decision, so re-enabling a person's attendance can take effect while they are still in view. No absence is inferred from a short camera disconnect. “Not checked in” means no check-in record for the selected local day.

Timestamps are server UTC confirmation times, displayed/exported in the configured IANA timezone. They are not hardware-camera capture timestamps. Keep the host clock synchronized when comparing devices.

## Storage and security boundary

SQLite stores metadata. Startup selects rollback journaling on runtimes affected by the SQLite WAL-reset race and enables WAL on fixed releases. Fernet encrypts embeddings, retained crops, and source URLs before persistence. The key is a separate local file. Memory contains plaintext frames and vectors while running. Metadata uses ordinary SQLite storage. The app assumes the local operating-system account and server host are trusted. [SQLite WAL-reset notice](https://www.sqlite.org/wal.html#the_wal_reset_bug)

One administrator role is supported. Initial setup is loopback-only, passwords use PBKDF2-HMAC-SHA256 with a random salt, and sessions are hashed server-side with 12-hour expiry and HttpOnly/SameSite cookies. Host validation, same-origin checks, restrictive browser content policy, and authenticated snapshot routes protect the local UI. HTTP is bound to loopback by the supplied launcher. Do not expose this server directly on the internet or bypass host/origin restrictions as a remote-access solution.

IP stream access is explicitly configured by the administrator; no discovery/network scan runs. Network sources permit RTSP, RTSPS, HTTP, HTTPS; local files and arbitrary URL schemes are not camera inputs. USB indices are 0–20. Native connection log verbosity is suppressed to reduce credential exposure. Returned camera data contains only a hostname/device hint; encrypted source URLs are never returned after saving.

Unknown crops/templates expire after configured retention, on a 60-second background purge and on relevant accesses. Collection off deletes unknown groups and samples. Full video is never written to disk. Naming keeps verified templates, removing source crops; automatic update evidence remains until rollback/person deletion. Metadata sightings/attendance remain until their person is forgotten or the database is removed. Hard-delete cascades through that person's templates, evidence, attendance, and sightings. Audit retains internal IDs and operation summaries, not copies of the face template. Database/WAL remnants and backups require operating-system storage lifecycle controls; the application does not claim forensic erasure.

If the administrator enables unknown collection, recent review samples can also carry an encrypted 112×112 alignment crop so naming can prepare the second model's templates. That crop follows the review sample's expiration and is removed when the sample is deleted or named.

## Deployment boundaries

Current target: a small organization on one Windows CPU host, up to eight configured cameras, with actual throughput measured on-site. Postgres/pgvector, independent capture services, GPU inference, ONVIF discovery, person-body tracking, multi-user roles, and production external key management are future extensions, not implemented features. Swapping SFace for AuraFace requires model/version separation and verified re-enrollment; their embedding spaces must never be mixed.
