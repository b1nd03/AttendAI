import {
  Camera,
  Plus,
  ScanFace,
  Settings2,
  Trash2,
  UserRound,
  UserRoundPlus,
  Video,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  api,
  errorMessage,
  json,
  localDay,
  preciseTime,
  timeLabel,
} from "./api";
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  PageHeading,
  Pagination,
  RouteSidebar,
  SearchField,
  useToast,
} from "./components";
import { CameraDialog } from "./camera-dialog";
import { CameraPreview, LaptopPreview } from "./camera-feed";
import { LaptopCameraDialog } from "./laptop-camera";
import type { CameraMode, CameraSource, Cluster, Sighting } from "./types";
import type { ViewProps } from "./views";

const roleLabel = (c: CameraSource) =>
  ({
    in: "Automatic check-in",
    out: "Automatic check-out",
    entry_exit: "Entrance and exit",
    observe: "Sightings only",
  })[c.role];
const stateLabel = (state: string) =>
  ({
    running: "Live",
    connecting: "Connecting",
    reconnecting: "Retrying",
    stopped: "Stopped",
    stopping: "Stopping",
  })[state] || state;
const cameraStateLabel = (camera: CameraSource) => {
  if (camera.state !== "running") return stateLabel(camera.state);
  if (!camera.preview_ready) return "Waiting for preview";
  if (camera.operation_mode === "preview") return "Preview only";
  if (camera.operation_mode === "setup") return "Setup test";
  return camera.analysis_state === "current" ? "Live" : "Preview live";
};
const operationLabel = (camera: CameraSource) =>
  camera.operation_mode === "setup"
    ? "Setup test · attendance paused"
    : camera.operation_mode === "preview"
      ? "Preview only · analysis paused"
      : roleLabel(camera);
function frameSizeLabel(
  source: CameraSource["source_size"],
  analysis: CameraSource["analysis_size"],
) {
  return [
    source && `Camera ${source.join(" × ")}`,
    analysis && `Analysis ${analysis.join(" × ")}`,
  ]
    .filter(Boolean)
    .join(" · ");
}
const sourceLabel = (c: CameraSource) =>
  c.source_type === "browser"
    ? "Browser"
    : c.source_type === "usb"
      ? "USB"
      : "IP";
const groupLabel = (id: string) => `#${id.slice(0, 6)}`;
type LiveFace = CameraSource["tracks"][number] & {
  key: string;
  camera: CameraSource;
};

function orderLiveFaces(cameras: CameraSource[], previous: LiveFace[]) {
  const current = new Map(
    cameras.flatMap((camera) =>
      camera.analysis_state === "current" && camera.operation_mode !== "setup"
        ? camera.tracks.map((track) => {
            const key = `${camera.id}/${track.track}`;
            return [key, { ...track, key, camera }] as const;
          })
        : [],
    ),
  );
  const unchanged = previous.flatMap((face) => {
    const updated = current.get(face.key);
    if (
      !updated ||
      updated.state !== face.state ||
      updated.label !== face.label
    )
      return [];
    current.delete(face.key);
    return [updated];
  });
  return [...current.values()].reverse().concat(unchanged);
}

function liveFaceLabel(face: LiveFace) {
  if (face.face_pixels != null && face.required_face_pixels != null)
    return `Face too small · ${face.face_pixels}/${face.required_face_pixels} px`;
  switch (face.review_status) {
    case "collecting":
      return `Collecting samples · ${face.review_samples ?? 0}/${face.needed ?? 3}`;
    case "ready":
      return "Face ready to name";
    case "dismissed":
      return "Face group removed";
    case "full":
      return "Review queue full";
    case "off":
      return "Unknown face · saving off";
    default:
      return face.label;
  }
}

type CameraSeed = {
  type: "network" | "usb";
  source: string;
  name: string;
  location?: string;
};

export function CameraMonitor({
  data,
  navigate,
  refresh,
  laptop,
  onEnroll,
  onManual,
}: ViewProps) {
  const [cameras, setCameras] = useState<CameraSource[]>([]);
  const [liveFaces, setLiveFaces] = useState<LiveFace[]>([]);
  const [groups, setGroups] = useState<Cluster[]>([]);
  const [groupTotal, setGroupTotal] = useState(0);
  const [events, setEvents] = useState<Sighting[]>([]);
  const [eventTotal, setEventTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [add, setAdd] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [seed, setSeed] = useState<CameraSeed>();
  const [manage, setManage] = useState<CameraSource | null>(null);
  const [error, setError] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [timelineError, setTimelineError] = useState("");
  const [reviewLoading, setReviewLoading] = useState(true);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [reviewRetry, setReviewRetry] = useState(0);
  const [timelineRetry, setTimelineRetry] = useState(0);
  const [manageError, setManageError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [detectionView, setDetectionView] = useState<Record<string, boolean>>(
    {},
  );
  const [reload, setReload] = useState(0);
  const [timelineOpen, setTimelineOpen] = useState(false);
  const toast = useToast();
  const zone = data.settings.timezone;
  const visibleReviewGroups = [
    ...new Set(liveFaces.map((face) => face.review_group_id).filter(Boolean)),
  ]
    .sort()
    .join(",");

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let interval = 1500;
      try {
        if (document.hidden) return;
        const sources = await api<{ items: CameraSource[] }>("/cameras", {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setCameras(sources.items);
          setLiveFaces((current) => orderLiveFaces(sources.items, current));
          setError("");
          setLoading(false);
          if (sources.items.some((camera) => camera.enabled)) interval = 450;
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(errorMessage(e));
          setLoading(false);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, interval);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await api<{ items: Cluster[]; total: number }>(
          "/review?summary=true",
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) {
          setGroups(response.items);
          setGroupTotal(response.total);
          setReviewError("");
        }
      } catch (e) {
        if (!controller.signal.aborted)
          setReviewError(
            `The review queue could not refresh. ${errorMessage(e)}`,
          );
      } finally {
        if (!controller.signal.aborted) setReviewLoading(false);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 3500);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [reload, reviewRetry, visibleReviewGroups]);

  useEffect(() => {
    if (!timelineOpen) return;
    const controller = new AbortController();
    setTimelineLoading(true);
    setTimelineError("");
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const params = new URLSearchParams({
          page: String(page),
          camera_id: filter,
          q: query,
          day: localDay(zone),
        });
        const response = await api<{
          items: Sighting[];
          total: number;
          page: number;
        }>(`/sightings?${params}`, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setEvents(response.items);
          setEventTotal(response.total);
          setTimelineError("");
          if (response.page !== page) setPage(response.page);
        }
      } catch (e) {
        if (!controller.signal.aborted)
          setTimelineError(
            `Recent sightings could not refresh. ${errorMessage(e)}`,
          );
      } finally {
        if (!controller.signal.aborted) setTimelineLoading(false);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2200);
    }
    timer = setTimeout(poll, query ? 300 : 0);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [filter, page, query, reload, timelineOpen, timelineRetry, zone]);

  function openCamera(initial?: CameraSeed) {
    setSeed(initial);
    setAdd(true);
  }
  function closeCamera() {
    setAdd(false);
    setSeed(undefined);
  }
  async function command(camera: CameraSource, action: "start" | "stop") {
    if (camera.source_type === "browser" && action === "start") {
      setManage(null);
      setSharing(true);
      return;
    }
    setBusy(camera.id);
    setManageError("");
    try {
      await api(`/cameras/${camera.id}/${action}`, json("POST"));
      if (camera.id === laptop.shareId && action === "stop") laptop.stop();
      toast(
        action === "start"
          ? "Camera is connecting."
          : "Camera monitoring stopped.",
      );
      setReload((v) => v + 1);
      setManage(null);
    } catch (e) {
      setManageError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function setDistanceMode(camera: CameraSource, enabled: boolean) {
    setBusy(camera.id);
    setManageError("");
    try {
      await api(`/cameras/${camera.id}/distance`, json("PATCH", { enabled }));
      setCameras((items) =>
        items.map((item) =>
          item.id === camera.id ? { ...item, distance_mode: enabled } : item,
        ),
      );
      setReload((value) => value + 1);
    } catch (e) {
      setManageError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function setCameraMode(camera: CameraSource, mode: CameraMode) {
    setBusy(camera.id);
    setManageError("");
    try {
      const updated = await api<CameraSource>(
        `/cameras/${camera.id}/mode`,
        json("PATCH", { mode }),
      );
      setCameras((items) =>
        items.map((item) => (item.id === camera.id ? updated : item)),
      );
      setLiveFaces((faces) =>
        faces.filter((face) => face.camera.id !== camera.id),
      );
      setReload((value) => value + 1);
      toast(
        mode === "live"
          ? "Live matching enabled."
          : mode === "setup"
            ? "Setup test started. Attendance is paused on this camera."
            : "Preview only enabled. Analysis is paused on this camera.",
      );
      return true;
    } catch (e) {
      setManageError(errorMessage(e));
      return false;
    } finally {
      setBusy("");
    }
  }
  const liveCount = cameras.filter(
    (c) => c.state === "running" && c.preview_ready,
  ).length;
  const analyzingCount = cameras.filter(
    (c) => c.analysis_state === "current",
  ).length;
  const enrolledCount = data.stats.enrolled;
  const matchingPaused =
    cameras.length > 0 &&
    cameras.every(
      (camera) =>
        camera.role === "observe" ||
        camera.operation_mode === "setup" ||
        camera.operation_mode === "preview",
    );
  const matchingStatus = error
    ? "Status unavailable"
    : loading
      ? "Checking cameras"
      : !cameras.length
        ? "Connect a camera"
        : matchingPaused
          ? "Paused on all cameras"
          : data.engine.warming
            ? "Starting"
            : !data.engine.recognition
              ? "Face matching unavailable"
              : !data.engine.liveness
                ? "Liveness check unavailable"
                : !enrolledCount
                  ? "Add a person first"
                  : cameras.some(
                        (camera) =>
                          camera.analysis_state === "current" &&
                          camera.role !== "observe" &&
                          (!camera.operation_mode ||
                            camera.operation_mode === "live"),
                      )
                    ? "Ready"
                    : "Waiting for a live camera";
  const currentCamera = manage
    ? (cameras.find((c) => c.id === manage.id) ?? manage)
    : null;
  const latestFace =
    !error &&
    (liveFaces.find(
      ({ state }) =>
        state === "recognized" || state === "candidate" || state === "unknown",
    ) ??
      liveFaces[0]);
  const latestReviewGroup = latestFace ? latestFace.review_group_id : undefined;

  return (
    <>
      <RouteSidebar>
        <CameraSidebar
          cameras={cameras}
          onManage={(c) => {
            setManageError("");
            setManage(c);
          }}
        />
      </RouteSidebar>
      <div className="attend-monitor">
        <header className="attend-monitor-header">
          <PageHeading
            section="Camera desk"
            title="Live Monitor"
            description="Watch arrivals and review faces that need a name."
            actions={
              <>
                <Button
                  onClick={() => setSharing(true)}
                  disabled={!!laptop.shareId}
                  variant={cameras.length ? "secondary" : "primary"}
                >
                  <Camera size={17} />
                  Share laptop camera
                </Button>
                <Button
                  variant={cameras.length ? "primary" : "secondary"}
                  onClick={() => openCamera()}
                >
                  <Plus size={17} />
                  Connect a camera
                </Button>
              </>
            }
          />
          <section
            className="panel monitor-readiness"
            aria-label="Attendance readiness"
          >
            <dl>
              <div>
                <dt>Cameras</dt>
                <dd>
                  {loading
                    ? "Checking cameras"
                    : error
                      ? "Camera status unavailable"
                      : `${liveCount} live · ${analyzingCount} analyzing`}
                </dd>
              </div>
              <div>
                <dt>People</dt>
                <dd>
                  {enrolledCount
                    ? `${enrolledCount} enrolled`
                    : "No people enrolled yet"}
                </dd>
              </div>
              <div>
                <dt>Automatic attendance</dt>
                <dd>{matchingStatus}</dd>
              </div>
            </dl>
            {(matchingPaused || (cameras.length > 0 && !enrolledCount)) && (
              <div className="monitor-enrollment-note">
                <p>
                  {matchingPaused
                    ? "Open a camera’s settings to enable attendance after testing the view."
                    : data.settings.retain_unknowns
                      ? "Collect three usable samples, then name the face to start recording attendance."
                      : "Enroll someone to let the camera recognize them and record attendance."}
                </p>
                {!enrolledCount && (
                  <Button
                    variant="secondary"
                    onClick={
                      groupTotal > 0 ? () => navigate("review") : onEnroll
                    }
                  >
                    {groupTotal > 0 ? "Review saved faces" : "Enroll a person"}
                  </Button>
                )}
              </div>
            )}
          </section>
          {!sharing && laptop.error && !laptop.shareId && (
            <ErrorBox message={laptop.error} retry={() => setSharing(true)} />
          )}
          {error && (
            <ErrorBox message={error} retry={() => setReload((v) => v + 1)} />
          )}
        </header>
        {cameras.length > 0 && (
          <section
            className="monitor-live-strip"
            aria-label="Live people and review"
          >
            <div className="monitor-live-entry">
              <span className="monitor-signal-icon" aria-hidden="true">
                <UserRound size={19} />
              </span>
              <div
                className="monitor-signal-copy"
                aria-live="polite"
                aria-atomic="true"
              >
                <span className="monitor-live-caption">
                  Latest arrival
                  {!error &&
                    liveFaces.length > 1 &&
                    ` · ${liveFaces.length} in view`}
                </span>
                <strong>
                  {latestFace
                    ? liveFaceLabel(latestFace)
                    : error
                      ? "Live updates unavailable"
                      : "No faces in view"}
                </strong>
                {latestFace && (
                  <span className="monitor-live-camera">
                    {latestFace.camera.name}
                  </span>
                )}
              </div>
              {latestFace && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    const tile = document.getElementById(
                      `monitor-camera-${latestFace.camera.id}`,
                    );
                    tile?.focus({ preventScroll: true });
                    tile?.scrollIntoView({ block: "start" });
                  }}
                >
                  View camera
                </Button>
              )}
            </div>
            <div className="monitor-live-entry">
              <span className="monitor-signal-icon" aria-hidden="true">
                <UserRoundPlus size={19} />
              </span>
              <div className="monitor-signal-copy">
                <span className="monitor-live-caption">Unnamed faces</span>
                <strong>
                  {reviewError
                    ? "Review unavailable"
                    : reviewLoading
                      ? "Checking review queue"
                      : `${groupTotal} waiting`}
                </strong>
              </div>
              <Button
                variant="secondary"
                onClick={() =>
                  navigate("review", latestReviewGroup ?? groups[0]?.id)
                }
              >
                {latestReviewGroup ? "Name live face" : "Open review"}
              </Button>
            </div>
          </section>
        )}
        <div className="attend-monitor-layout">
          <section className="attend-monitor-main" aria-label="Camera wall">
            {cameras.length > 0 && (
              <div className="camera-wall-heading">
                <h2>Camera wall</h2>
                <span>
                  {liveCount} of {cameras.length} live
                </span>
              </div>
            )}
            {loading ? (
              <Loading label="Loading camera sources" />
            ) : cameras.length ? (
              <div className="attend-camera-grid">
                {cameras.map((camera) => (
                  <article
                    className="attend-camera-tile"
                    key={camera.id}
                    aria-label={camera.name}
                    id={`monitor-camera-${camera.id}`}
                    tabIndex={-1}
                  >
                    <header className="camera-tile-heading">
                      <div>
                        <h2>{camera.name}</h2>
                        <p>{camera.location}</p>
                      </div>
                      <Badge
                        tone={
                          camera.analysis_state === "current"
                            ? "success"
                            : camera.state === "reconnecting"
                              ? "warning"
                              : "neutral"
                        }
                      >
                        {cameraStateLabel(camera)}
                      </Badge>
                    </header>
                    <div className="camera-tile-feed">
                      {camera.id === laptop.shareId &&
                      (camera.operation_mode === "preview" ||
                        !detectionView[camera.id]) ? (
                        <LaptopPreview source={laptop.videoRef} />
                      ) : camera.preview_ready ? (
                        <CameraPreview
                          key={String(
                            camera.operation_mode !== "preview" &&
                              !!detectionView[camera.id],
                          )}
                          id={camera.id}
                          name={camera.name}
                          analyzed={
                            camera.operation_mode !== "preview" &&
                            !!detectionView[camera.id]
                          }
                        />
                      ) : (
                        <div className="attend-feed-empty">
                          <Video size={28} />
                          <strong>
                            {camera.state === "running"
                              ? "Preparing the live preview"
                              : stateLabel(camera.state)}
                          </strong>
                          <span>
                            {camera.error ||
                              (camera.source_type === "browser" &&
                              !camera.enabled
                                ? "Share your laptop camera to reconnect."
                                : camera.source_hint)}
                          </span>
                        </div>
                      )}
                    </div>
                    <footer className="camera-tile-footer">
                      <div>
                        <span>{operationLabel(camera)}</span>
                        {camera.analysis_state === "current" && (
                          <small>
                            {camera.tracks.length}{" "}
                            {camera.tracks.length === 1 ? "face" : "faces"} in
                            view
                          </small>
                        )}
                        {camera.distance_mode &&
                          camera.id === laptop.shareId &&
                          laptop.sourceSize &&
                          Math.max(...laptop.sourceSize) < 1920 && (
                            <small>
                              High detail requested. This camera is supplying{" "}
                              {laptop.sourceSize.join(" × ")}.
                            </small>
                          )}
                      </div>
                      {camera.preview_ready &&
                        camera.operation_mode !== "preview" && (
                          <button
                            type="button"
                            className="icon-button"
                            aria-label={`Detection boxes for ${camera.name}`}
                            aria-pressed={!!detectionView[camera.id]}
                            title={
                              detectionView[camera.id]
                                ? "Return to live video"
                                : "Show detection boxes"
                            }
                            onClick={() =>
                              setDetectionView((shown) => ({
                                ...shown,
                                [camera.id]: !shown[camera.id],
                              }))
                            }
                          >
                            <ScanFace size={19} />
                          </button>
                        )}
                      {camera.source_type === "browser" && !camera.enabled ? (
                        <Button
                          variant="secondary"
                          onClick={() => setSharing(true)}
                        >
                          Share again
                        </Button>
                      ) : (
                        <button
                          className="icon-button"
                          aria-label={`Manage ${camera.name}`}
                          title="Camera settings"
                          onClick={() => {
                            setManageError("");
                            setManage(camera);
                          }}
                        >
                          <Settings2 size={19} />
                        </button>
                      )}
                    </footer>
                    {camera.preview_ready && camera.error && (
                      <p className="camera-person-note" role="status">
                        {camera.error}
                      </p>
                    )}
                    {camera.preview_ready &&
                      camera.analysis_state === "delayed" && (
                        <p className="camera-person-note" role="status">
                          Live video is arriving. Face analysis is delayed.
                        </p>
                      )}
                    {camera.preview_ready &&
                      camera.analysis_state === "starting" && (
                        <p className="camera-person-note">
                          Starting face analysis…
                        </p>
                      )}
                    {camera.people_count > 0 && camera.tracks.length === 0 && (
                      <p className="camera-person-note">
                        People detected. Waiting for a visible face.
                      </p>
                    )}
                    {camera.tracks.length > 0 && (
                      <ul
                        className="camera-track-labels"
                        aria-label="Faces in view"
                      >
                        {camera.tracks.slice(0, 4).map((track) => (
                          <li key={track.track}>
                            <Badge
                              tone={
                                track.state === "recognized" ||
                                track.state === "setup_usable"
                                  ? "success"
                                  : track.state === "verifying"
                                    ? "neutral"
                                    : "warning"
                              }
                            >
                              {track.label}
                            </Badge>
                            {track.message && <small>{track.message}</small>}
                            {track.manual_kind && track.person_id && (
                              <Button
                                variant="secondary"
                                onClick={() => {
                                  if (track.person_id && track.manual_kind)
                                    onManual({
                                      personId: track.person_id,
                                      kind: track.manual_kind,
                                    });
                                }}
                              >
                                Confirm{" "}
                                {track.manual_kind === "in"
                                  ? "check-in"
                                  : "check-out"}
                              </Button>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </article>
                ))}
              </div>
            ) : (
              <section className="panel">
                <Empty
                  title="Connect your first camera"
                  description="Try the camera on this laptop or connect one from your facility. Arrivals will appear here when a camera is live."
                  icon={<Camera size={26} />}
                />
              </section>
            )}
            <details
              className="attend-sightings"
              onToggle={(e) => setTimelineOpen(e.currentTarget.open)}
            >
              <summary>Recent sightings</summary>
              <div className="attend-sighting-filters">
                <SearchField
                  value={query}
                  onChange={(v) => {
                    setQuery(v);
                    setPage(1);
                  }}
                  placeholder="Find a person or camera"
                />
                <select
                  aria-label="Filter sightings by camera"
                  value={filter}
                  onChange={(e) => {
                    setFilter(e.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">All cameras</option>
                  {cameras.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
              {timelineError && (
                <ErrorBox
                  message={timelineError}
                  retry={() => setTimelineRetry((n) => n + 1)}
                />
              )}
              {timelineLoading && !events.length ? (
                <Loading label="Loading recent sightings" />
              ) : events.length ? (
                <ul className="attend-events">
                  {events.map((event) => (
                    <li key={event.id}>
                      <div>
                        <strong>{event.name || "Unknown person"}</strong>
                        <span>
                          {event.camera_name} · {event.location}
                        </span>
                      </div>
                      <span>
                        {event.attendance_kind
                          ? `Checked ${event.attendance_kind}`
                          : event.state === "candidate"
                            ? "Needs verification"
                            : event.state === "named"
                              ? "Named after review"
                              : event.state === "unknown"
                                ? "Unnamed"
                                : "Sighted"}
                      </span>
                      <time className="mono" dateTime={event.created_at}>
                        {preciseTime(event.created_at, zone)}
                      </time>
                    </li>
                  ))}
                </ul>
              ) : !timelineError ? (
                <p className="attend-muted attend-sighting-empty">
                  No sightings match this view.
                </p>
              ) : null}
              <Pagination
                page={page}
                total={eventTotal}
                size={20}
                onChange={setPage}
              />
            </details>
          </section>
          <aside className="attend-unnamed" aria-label="Newly seen people">
            <div className="attend-queue-heading">
              <h2>Unnamed faces</h2>
              <span className="mono attend-warning">{groupTotal}</span>
            </div>
            <p className="attend-muted">
              {data.settings.retain_unknowns
                ? "Saved faces appear here. Name them when you are ready."
                : "Turn on Save unknown faces when sharing a camera, or enable it in Settings."}
            </p>
            {reviewError && (
              <ErrorBox
                message={reviewError}
                retry={() => setReviewRetry((n) => n + 1)}
              />
            )}
            {reviewLoading && !groups.length && (
              <Loading label="Loading unnamed faces" />
            )}
            <ul className="attend-quick-queue">
              {groups.slice(0, 4).map((group) => (
                <li key={group.id}>
                  {group.sample ? (
                    <img
                      src={`data:image/jpeg;base64,${group.sample}`}
                      alt=""
                    />
                  ) : (
                    <span className="attend-question" aria-hidden="true">
                      ?
                    </span>
                  )}
                  <div>
                    <strong>{groupLabel(group.id)}</strong>
                    <span>
                      {group.camera_name || "Camera"} ·{" "}
                      {preciseTime(group.last_seen, zone)} ·{" "}
                      {group.sample_count ?? group.samples?.length ?? 1} samples
                    </span>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => navigate("review", group.id)}
                    aria-label={`Name ${groupLabel(group.id)}`}
                  >
                    Name
                  </Button>
                </li>
              ))}
            </ul>
            {!groups.length && !reviewError && !reviewLoading && (
              <p className="attend-muted attend-queue-empty">
                No faces waiting for a name.
              </p>
            )}
            {!!groups.length && (
              <Button
                className="attend-review-all"
                onClick={() => navigate("review")}
              >
                Review unnamed faces
              </Button>
            )}
          </aside>
        </div>
      </div>
      <LaptopCameraDialog
        open={sharing}
        camera={laptop}
        collecting={data.settings.retain_unknowns}
        onClose={() => setSharing(false)}
        onShared={() => {
          setSharing(false);
          setReload((value) => value + 1);
          refresh();
          toast("Laptop camera is sharing.");
        }}
        onDirect={() => {
          laptop.clearError();
          setSharing(false);
          openCamera({
            type: "usb",
            source: "0",
            name: "Laptop camera",
            location: "This computer",
          });
        }}
      />
      <CameraDialog
        open={add}
        initial={seed}
        onClose={closeCamera}
        onSaved={() => {
          closeCamera();
          setReload((v) => v + 1);
          refresh();
        }}
      />
      <Modal
        open={!!currentCamera}
        onClose={() => {
          if (!busy) setManage(null);
        }}
        title={
          currentCamera ? `Manage ${currentCamera.name}` : "Camera details"
        }
      >
        {currentCamera && (
          <>
            <dl className="camera-details">
              <div>
                <dt>Room</dt>
                <dd>{currentCamera.location}</dd>
              </div>
              <div>
                <dt>Live matching role</dt>
                <dd>{roleLabel(currentCamera)}</dd>
              </div>
              <div>
                <dt>Connection</dt>
                <dd>{cameraStateLabel(currentCamera)}</dd>
              </div>
            </dl>
            <CameraOperation
              key={currentCamera.id}
              camera={currentCamera}
              busy={!!busy}
              onApply={(mode) => setCameraMode(currentCamera, mode)}
            />
            <details className="camera-advanced">
              <summary>Advanced camera settings</summary>
              <dl className="camera-details">
                <div>
                  <dt>Source</dt>
                  <dd>
                    {sourceLabel(currentCamera)} · {currentCamera.source_hint}
                  </dd>
                </div>
                {(currentCamera.source_size || currentCamera.analysis_size) && (
                  <div>
                    <dt>Frame size</dt>
                    <dd>
                      {frameSizeLabel(
                        currentCamera.id === laptop.shareId
                          ? (laptop.sourceSize ?? currentCamera.source_size)
                          : currentCamera.source_size,
                        currentCamera.analysis_size,
                      )}
                    </dd>
                  </div>
                )}
                {currentCamera.analysis_state === "current" && (
                  <div>
                    <dt>Analysis</dt>
                    <dd>
                      {currentCamera.processed_fps} fps ·{" "}
                      {currentCamera.latency_ms} ms
                    </dd>
                  </div>
                )}
                {currentCamera.distance_mode && currentCamera.preview_ready && (
                  <div>
                    <dt>People detected</dt>
                    <dd>{currentCamera.people_count}</dd>
                  </div>
                )}
              </dl>
              {currentCamera.source_type !== "browser" && (
                <label className="check-field">
                  <input
                    type="checkbox"
                    checked={currentCamera.distance_mode}
                    disabled={!!busy}
                    onChange={(e) =>
                      void setDistanceMode(currentCamera, e.target.checked)
                    }
                  />
                  <span>
                    <strong>Room-distance detection</strong>
                    <br />
                    Use the main stream to find people and look for faces at a
                    distance.
                  </span>
                </label>
              )}
            </details>
            {manageError && <ErrorBox message={manageError} />}
            <div className="modal-actions">
              <Button variant="secondary" onClick={() => setManage(null)}>
                Close
              </Button>
              <Button
                disabled={currentCamera.state === "stopping"}
                busy={!!busy}
                onClick={() =>
                  void command(
                    currentCamera,
                    currentCamera.enabled ? "stop" : "start",
                  )
                }
              >
                {currentCamera.enabled ? "Stop camera" : "Start camera"}
              </Button>
            </div>
            <details className="attend-remove-camera">
              <summary>Remove camera</summary>
              <p>
                Remove this connection and its saved credentials. Sightings and
                attendance records stay in the workspace.
              </p>
              <Button
                variant="danger"
                busy={!!busy}
                onClick={async () => {
                  setBusy(currentCamera.id);
                  try {
                    await api(`/cameras/${currentCamera.id}`, json("DELETE"));
                    if (currentCamera.id === laptop.shareId) laptop.stop();
                    setManage(null);
                    setReload((v) => v + 1);
                    toast("Camera removed.");
                  } catch (e) {
                    setManageError(errorMessage(e));
                  } finally {
                    setBusy("");
                  }
                }}
              >
                <Trash2 size={15} />
                Remove camera
              </Button>
            </details>
          </>
        )}
      </Modal>
    </>
  );
}

function CameraOperation({
  camera,
  busy,
  onApply,
}: {
  camera: CameraSource;
  busy: boolean;
  onApply: (mode: CameraMode) => Promise<boolean>;
}) {
  const mode = camera.operation_mode ?? "live";
  const [selection, setSelection] = useState<CameraMode | null>(null);
  const next = selection ?? mode;
  const test = camera.setup_test;
  return (
    <section
      className="camera-operation"
      aria-labelledby="camera-operation-heading"
    >
      <h3 id="camera-operation-heading">Camera use</h3>
      <p>{operationLabel(camera)}</p>
      {mode === "setup" && (
        <div className="camera-test-results">
          <p>Walk through the intended check-in point at your normal pace.</p>
          {test ? (
            <>
              <dl>
                <div>
                  <dt>Frames with a face</dt>
                  <dd>
                    {test.face_frames} of {test.frames}
                  </dd>
                </div>
                <div>
                  <dt>Frames with a usable face</dt>
                  <dd>
                    {test.usable_frames} of {test.frames}
                  </dd>
                </div>
                <div>
                  <dt>Largest face measured</dt>
                  <dd>
                    {test.largest_face_pixels
                      ? `${test.largest_face_pixels} px`
                      : "—"}{" "}
                    · {test.required_face_pixels} px minimum
                  </dd>
                </div>
              </dl>
              <p>
                {frameSizeLabel(camera.source_size, camera.analysis_size) ||
                  "Waiting for current video."}
              </p>
              {test.latest_blocker && <p>{test.latest_blocker}</p>}
              <p>
                {test.face_frames === 0
                  ? "No faces detected. Walk toward the lens through the intended check-in point."
                  : test.largest_face_pixels > 0 &&
                      test.largest_face_pixels < test.required_face_pixels
                    ? "Try optical zoom or move the check-in point closer. Check room-distance mode if analysis is smaller than the incoming feed."
                    : "Usable frames passed the app’s size, lighting and sharpness checks. Test identity separately before relying on attendance."}
              </p>
            </>
          ) : (
            <p>
              Waiting for analyzed frames. Start or reconnect this camera to
              begin.
            </p>
          )}
        </div>
      )}
      <div className="camera-operation-controls">
        <Field id="manage-camera-mode" label="Operating mode">
          <select
            id="manage-camera-mode"
            value={next}
            disabled={busy}
            onChange={(event) => setSelection(event.target.value as CameraMode)}
          >
            <option value="setup">Setup test</option>
            <option value="preview">Preview only</option>
            <option value="live">Live matching</option>
          </select>
        </Field>
        <Button
          variant="secondary"
          disabled={next === mode}
          busy={busy}
          onClick={async () => {
            if (await onApply(next)) setSelection(null);
          }}
        >
          Apply mode
        </Button>
      </div>
      <p className="caption">
        {next === "setup"
          ? "Measures face detail and pauses face saving, sightings and attendance."
          : next === "preview"
            ? "Keeps video visible with face analysis and attendance paused."
            : `Applies this camera’s configured role. ${roleLabel(camera)}.`}
      </p>
    </section>
  );
}

function CameraSidebar({
  cameras,
  onManage,
}: {
  cameras: CameraSource[];
  onManage: (camera: CameraSource) => void;
}) {
  if (!cameras.length) return null;
  return (
    <details className="attend-camera-sidebar">
      <summary className="attend-sidebar-label">
        Camera list · {cameras.length}
      </summary>
      <ul className="attend-source-list">
        {cameras.map((camera) => (
          <li key={camera.id}>
            <button type="button" onClick={() => onManage(camera)}>
              <span>
                <strong>{camera.name}</strong>
                <small>{sourceLabel(camera)}</small>
              </span>
              <span
                className={
                  camera.analysis_state === "current"
                    ? "attend-success"
                    : camera.state === "reconnecting"
                      ? "attend-warning"
                      : "attend-muted"
                }
              >
                {cameraStateLabel(camera)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
