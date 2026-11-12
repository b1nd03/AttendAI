# UX contract

## Product context
AttendAI is a single-administrator local attendance application for a small organization. The interface uses en-GB formatting and Gregorian dates with a configurable IANA time zone. It targets WCAG 2.2 AA. The application supports continuous camera monitoring and administrator-verified enrollment. `docs/RESEARCH.md` records the model and lifecycle decisions. `backend/main.py` implements the API contract exposed at `/openapi.json`.

## Business-context sources
| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Permission model | docs/ARCHITECTURE.md | Local application policy | 2026-11-05 |
| Data lifecycle | docs/ARCHITECTURE.md | Domain contract | 2026-11-05 |
| Deletion / retention | docs/ARCHITECTURE.md | Domain contract | 2026-11-05 |
| Research rationale | docs/RESEARCH.md | Primary technical sources | 2026-11-05 |

## Visual contract
`DESIGN.md` mirrors the CSS variables in `src/styles.css`. Verify them with `scripts/check_tokens.py`. Shared UI in `src/components.tsx` uses a light theme with a dark navigation rail and support for forced colors and reduced motion.

## Canonical UI Map
| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Native HTML select, shared Field wrapper | This contract | native; OS popup accepted | E2E keyboard |
| Date | Native input type=date, shared Field or explicit label | This contract | native; OS calendar locale accepted | E2E records |
| Form | Field and controlled React forms | This contract + API schema | create / edit | E2E validation |
| Scrollbar | Global src/styles.css | DESIGN.md | geometry only | computed style |
| Toast | ToastProvider | This contract | success / error | E2E live region |
| CRUD | EnrollmentDialog / PersonDialog / api.ts | API + this contract | return-to-list / stay for settings | E2E CRUD |
| Disclosure | Native details and summary | This contract | advanced settings, diagnostics, optional evidence and row actions | keyboard and narrow E2E |
Table Selection is not applicable: no bulk mutations or row selection.

## Dataset navigation
Roster and review: server pagination, 12/page. Audit: server pagination, 20/page. Records: latest 200 events in selected day, 10/page, explicitly disclosed; CSV exports all events. Dashboard shows five recent events. Search is transient sensitive person information and is deliberately not placed in browser history or local storage. Route hashes are bookmarkable; list filters survive dialogs. Search resets page, remote requests debounce 300ms and cancel old results. Back navigation resets transient filters by design; no promise of restoring filters across routes. Directory data is intended for small organizations; all-person dashboard aggregation is a known scaling boundary.

## Flow ledger
| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome |
|---|---|---|---|---|---|---|
| Create | Enroll a person | Busy stable action | People list | Person enrolled | Keep inputs and captures, inline error | Route content |
| Edit | Save changes | Busy stable action | Stay in Settings | Settings saved | Keep values; 409 requires reload | Save control |
| Delete | Forget person | Busy confirmation | People list | Person forgotten | Keep dialog open | Trigger or route |
| Search | Search field | Scoped loading | Same list | Result range | Inline retry | Input |
| Cancel/back | Cancel / close | None | Originating view | None | Dirty form asks to discard | Original trigger |
| Rollback | Roll back template | Busy confirmation | Profile | Template rolled back | Keep profile open | Profile |
| Apply camera mode | Operating mode and Apply mode | Scoped busy action | Stay in Manage camera | Camera status and toast | Keep selected mode and show inline error | Manage camera dialog |

## Navigation and responsive behavior
Each route sets `{Page} — AttendAI`. Native links convey navigation; buttons perform actions. Mobile navigation is a Radix modal. Table columns scroll horizontally rather than hiding data. Main page is the vertical scroller; no fixed-height form clipping. Names wrap in cards and table buttons.

## Overlays and feedback
Radix Dialog owns inert background, focus trap, Escape, and restoration. Delete/rollback confirmation names consequences, focuses Cancel first, and commits only after server success. Dirty enrollment/manual forms require app-owned discard confirmation; Settings navigation and page unload warn about dirty values. No native alert/confirm/prompt. Toasts stay bottom-right for 5 seconds and are announced politely; actionable failures also remain inline. Dialog z-index 70/71, toast 100. No nested product dialogs are intentionally opened.

## Async and resilience
Mutations are pessimistic. Busy actions block duplicates; server checks uniqueness, attendance cooldown and current state. Settings have optimistic concurrency revision checks. No offline write queue, no biometric drafts in browser storage. Requests time out after 30s except a scanner request aborted on stop/unmount. Continuous cameras are server-owned: capture and latest-frame scheduling continue when the browser closes. Only explicit Stop or server shutdown pauses them. Private image previews poll without persisting images; changing route does not stop a background camera. Authentication uses an HttpOnly same-site session. A 401 opens sign-in without deleting existing form state. Local server failures show inline retry and preserve safe form data. Browser background refresh revalidates dashboard every 20 seconds; failed refresh keeps stale data with a visible error. One backend process owns all cameras; browser tabs view the same workers. Multi-process camera ownership is not supported.

## Validation and sensitive values
All forms use noValidate, associated labels, inline error text and first-invalid focus where field-level errors exist. Passwords mask by default; reveal is a labeled button. Credentials are never in URLs or local storage. Native select/date controls intentionally use browser/OS popup behavior. Enrollment accepts 3–5 JPEG/PNG images of <=5MB, server dimension/quality/identity checks, a visible picker and camera alternative. Captures remain in memory only. Settings numerical bounds are checked by the API and cross-field validation remains inline.

## Verification
Run `npm run build`, `npm run format:check`, `.venv/Scripts/python -m pytest`, `npm run test:e2e`, and `scripts/check_tokens.py`. Browser checks cover desktop and mobile layouts, keyboard navigation, camera connection states, server failures, empty states, tables, and automated accessibility. Backend tests use deterministic embeddings to exercise attendance safeguards. Site accuracy requires tests on the deployed cameras.

## Continuous monitoring and naming extension
The primary route is Live Monitor (#scanner). CameraMonitor owns source list/polling, private recent previews, and camera controls; FaceInbox owns grouped unknown samples and verified naming. Source configuration is saved stopped and starts only after Connect & start monitoring. RTSP credentials are masked and encrypted; the list API returns a host hint only. Removing a camera requires a consequence-bearing modal and retains sightings; stopping is a reversible operational action. Camera wall and sighting timeline use shared components and native select ownership above. Sighting filters are sensitive transient state and intentionally stay out of browser history. Server event pagination is 20/page.

Naming is a confirmed identity operation, not an automatic machine assertion: review all samples, enter name/ID/group, confirm consent and in-person verification, then save. Success returns to Name People with a shared toast and refreshes the directory. Failure preserves entered values. Unknown, candidate, recognized, and human-named states are distinct. Historical naming never backdates attendance.

Server-connected cameras keep monitoring after sign-out. Browser camera sharing ends. Face collection and learning options are visible when adding a camera, and enabling them is reflected in the versioned workspace settings. Enter connection secrets inside the local application.

Live Monitor places connection controls and attendance readiness above the camera wall, with unnamed groups beside the monitor. Face review pairs evidence with the naming form. Camera status distinguishes raw video from current face analysis. Review and sightings errors remain in their own regions and offer scoped retries. The naming selection remains pinned during editing. The attendance checkbox controls future automatic attendance for that identity and is editable in the People profile. Camera sightings continue for known people with automatic attendance paused.

Everyday screens emphasize arrivals, names and records. Camera frame sizes and latency are under Advanced camera settings. Face matching and learning thresholds are under Advanced settings. Attendance match scores and face sample quality use secondary disclosures. Opening or closing a disclosure preserves edits and camera settings.

Each named live face shows the attendance outcome or the reason it is pending. When liveness is pending on an eligible attendance camera, the operator can open the shared manual attendance dialog with the suggested person and direction filled in. The operator verifies the person and enters a reason before saving. The server applies the same duplicate checks to the manual entry.

The sticky live strip shows the latest resolved face and its camera. Pending detections are shown while there is no resolved face. The camera action moves keyboard focus and scrolls to the source tile. The adjacent review action opens Name People directly. A failed camera status poll replaces the face text with an unavailable message.

For a live unknown, the strip distinguishes capture progress, a saved review group, a removed group, a full queue and disabled saving. A committed review group refreshes the queue count and exposes **Name live face**, which selects that group even when it is on a later page. The live status never implies an unknown sighting alone is ready to name.

The laptop sharing form offers high detail as an opt-in capture mode. The camera card reports the actual capture and analysis sizes and explains when the webcam supplies a lower resolution. The sticky status distinguishes a detected face below the minimum size from a face being matched or saved for naming.

Combine unnamed groups opens a comparison dialog with the samples from both groups. It requires an explicit same-person confirmation. A successful response updates the selected group in place, keeps sighting history and clears the naming verification checkbox for the new sample set. A failed response keeps the comparison and verification available for review.
