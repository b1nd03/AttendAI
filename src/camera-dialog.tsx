import { Eye, EyeOff, LockKeyhole, Network, Usb, Video } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorMessage, json } from "./api";
import {
  Button,
  ErrorBox,
  Field,
  Modal,
  ModalCancel,
  useToast,
} from "./components";
import type { CameraMode, CameraSource } from "./types";
export function CameraDialog({
  open,
  onClose,
  onSaved,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  initial?: {
    type: "network" | "usb";
    source: string;
    name: string;
    location?: string;
  };
}) {
  const [type, setType] = useState<"network" | "usb">("network");
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [source, setSource] = useState("");
  const [role, setRole] = useState("in");
  const [trigger, setTrigger] = useState("appearance");
  const [axis, setAxis] = useState("x");
  const [position, setPosition] = useState(0.5);
  const [direction, setDirection] = useState(1);
  const [fps, setFps] = useState(5);
  const [distance, setDistance] = useState(false);
  const [mode, setMode] = useState<CameraMode>("setup");
  const [authorized, setAuthorized] = useState(false);
  const [collect, setCollect] = useState(false);
  const [learn, setLearn] = useState(false);
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => {
    if (!open) {
      setSource("");
      setShow(false);
      return;
    }
    if (open) {
      setName(initial?.name ?? "");
      setLocation(initial?.location ?? "");
      setSource(initial?.source ?? "");
      setType(initial?.type ?? "network");
      setRole("in");
      setTrigger("appearance");
      setDistance(false);
      setMode("setup");
      setCollect(false);
      setLearn(false);
      setAuthorized(false);
      setError("");
      setShow(false);
    }
  }, [open]);
  const dirty = !!name || !!location || !!source;
  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Connect a camera"
      description="Choose how your camera connects. You can test the view before enabling attendance."
      wide
      dirty={dirty}
    >
      <form
        noValidate
        onSubmit={async (e) => {
          e.preventDefault();
          if (
            !name.trim() ||
            !location.trim() ||
            !source.trim() ||
            !authorized
          ) {
            setError(
              "Enter a camera name, location, source, and confirm authorization.",
            );
            document
              .getElementById(
                !name.trim()
                  ? "camera-name"
                  : !location.trim()
                    ? "camera-location"
                    : !source.trim()
                      ? "camera-source"
                      : "camera-authorized",
              )
              ?.focus();
            return;
          }
          setBusy(true);
          setError("");
          try {
            const camera = await api<CameraSource>(
              "/cameras",
              json("POST", {
                name,
                location,
                source_type: type,
                source,
                role,
                trigger_mode: role === "entry_exit" ? "line" : trigger,
                line_axis: axis,
                line_position: position,
                direction,
                analysis_fps: fps,
                distance_mode: distance,
                operation_mode: mode,
                authorized,
                collect_unknowns: collect,
                improve_gallery: learn,
              }),
            );
            try {
              await api(`/cameras/${camera.id}/start`, json("POST"));
              toast(
                mode === "setup"
                  ? "Camera saved. Connecting in setup test mode."
                  : "Camera saved. Connecting in preview-only mode.",
              );
            } catch {
              toast(
                "Camera saved. Use Start on the camera card to reconnect.",
                "error",
              );
            }
            onSaved();
          } catch (e) {
            setError(errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="camera-form-grid">
          <div>
            <div className="source-options" aria-label="Camera connection type">
              <button
                type="button"
                aria-pressed={type === "network"}
                className={type === "network" ? "selected" : ""}
                onClick={() => {
                  setType("network");
                  setSource("");
                }}
              >
                <Network size={20} />
                <strong>Network camera</strong>
                <span>From your camera or recorder</span>
              </button>
              <button
                type="button"
                aria-pressed={type === "usb"}
                className={type === "usb" ? "selected" : ""}
                onClick={() => {
                  setType("usb");
                  setSource("0");
                }}
              >
                <Usb size={20} />
                <strong>USB webcam</strong>
                <span>Connected to this computer</span>
              </button>
            </div>
            <Field label="Camera name" id="camera-name">
              <input
                id="camera-name"
                value={name}
                maxLength={80}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Camera 1"
              />
            </Field>
            <Field label="Room or location" id="camera-location">
              <input
                id="camera-location"
                value={location}
                maxLength={80}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="e.g. Main entrance"
              />
            </Field>
            <Field
              label={type === "network" ? "Stream address" : "USB device index"}
              id="camera-source"
              hint={
                type === "network"
                  ? "Get the RTSP or MJPEG address from your camera or NVR. Include its username/password if required."
                  : "0 is usually the first webcam, 1 the second. Access comes from the server, not the browser."
              }
            >
              <div className="password-wrap">
                <input
                  id="camera-source"
                  autoComplete="off"
                  type={type === "usb" ? "number" : show ? "text" : "password"}
                  min="0"
                  max="20"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder={
                    type === "network"
                      ? "rtsp://user:password@192.168.1.10:554/stream"
                      : "0"
                  }
                />
                {type === "network" && (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={
                      show ? "Hide stream address" : "Show stream address"
                    }
                    aria-pressed={show}
                    onClick={() => setShow(!show)}
                  >
                    {show ? <EyeOff size={17} /> : <Eye size={17} />}
                  </button>
                )}
              </div>
            </Field>
            <Field label="Start camera in" id="camera-operation-mode">
              <select
                id="camera-operation-mode"
                value={mode}
                onChange={(e) => setMode(e.target.value as CameraMode)}
              >
                <option value="setup">Setup test</option>
                <option value="preview">Preview only</option>
              </select>
            </Field>
            <div className="privacy-note">
              <LockKeyhole size={18} />
              <p>
                Stream credentials are encrypted locally and never returned to
                the browser after saving. No full video is recorded.
              </p>
            </div>
          </div>
          <details className="camera-advanced">
            <summary>Attendance and camera options</summary>
            <label className="check-field">
              <input
                type="checkbox"
                checked={distance}
                onChange={(e) => setDistance(e.target.checked)}
              />
              <span>
                <strong>Room-distance detection</strong>
                <br />
                Find people first and use high-resolution crops to look for
                faces. Best with the camera’s main stream.
              </span>
            </label>
            <Field label="When a known person appears" id="camera-role">
              <select
                id="camera-role"
                value={role}
                onChange={(e) => {
                  setRole(e.target.value);
                  if (e.target.value === "entry_exit") setTrigger("line");
                }}
              >
                <option value="in">Record check-in automatically</option>
                <option value="out">Record check-out automatically</option>
                <option value="entry_exit">
                  Record entrance and exit by direction
                </option>
                <option value="observe">Log sightings only</option>
              </select>
            </Field>
            <Field label="Attendance trigger" id="camera-trigger">
              <select
                id="camera-trigger"
                value={role === "entry_exit" ? "line" : trigger}
                disabled={role === "entry_exit"}
                onChange={(e) => setTrigger(e.target.value)}
              >
                <option value="appearance">
                  First stable sighting in the room
                </option>
                <option value="line">Crossing a virtual entrance line</option>
              </select>
            </Field>
            {(trigger === "line" || role === "entry_exit") && (
              <>
                <div className="field-row">
                  <Field label="Line orientation" id="camera-axis">
                    <select
                      id="camera-axis"
                      value={axis}
                      onChange={(e) => setAxis(e.target.value)}
                    >
                      <option value="x">Vertical line</option>
                      <option value="y">Horizontal line</option>
                    </select>
                  </Field>
                  <Field label="Line position (%)" id="camera-position">
                    <input
                      id="camera-position"
                      type="number"
                      min="10"
                      max="90"
                      value={Math.round(position * 100)}
                      onChange={(e) =>
                        setPosition(Number(e.target.value) / 100)
                      }
                    />
                  </Field>
                </div>
                <Field label="Entrance direction" id="camera-direction">
                  <select
                    id="camera-direction"
                    value={direction}
                    onChange={(e) => setDirection(Number(e.target.value))}
                  >
                    <option value="1">
                      {axis === "x" ? "Left → right" : "Top → bottom"}
                    </option>
                    <option value="-1">
                      {axis === "x" ? "Right → left" : "Bottom → top"}
                    </option>
                  </select>
                </Field>
              </>
            )}
            <Field
              label="Requested analysis rate"
              id="camera-fps"
              hint="Latest frames are prioritized. Actual rate depends on the CPU, camera count, and number of visible faces."
            >
              <select
                id="camera-fps"
                value={fps}
                onChange={(e) => setFps(Number(e.target.value))}
              >
                {[1, 2, 3, 5, 8].map((n) => (
                  <option key={n} value={n}>
                    {n} frames / second
                  </option>
                ))}
              </select>
            </Field>
            <label className="check-field">
              <input
                type="checkbox"
                checked={collect}
                onChange={(e) => setCollect(e.target.checked)}
              />
              <span>
                Collect unknown face groups so I can name and verify people in
                the face inbox.
              </span>
            </label>
            <label className="check-field">
              <input
                type="checkbox"
                checked={learn}
                onChange={(e) => setLearn(e.target.checked)}
              />
              <span>
                Improve known faces using strictly verified matches and fixed
                enrollment anchors.
              </span>
            </label>
          </details>
        </div>
        <label className="check-field camera-consent">
          <input
            id="camera-authorized"
            type="checkbox"
            checked={authorized}
            onChange={(e) => setAuthorized(e.target.checked)}
          />
          <span>
            I am authorized to connect this camera and have arranged the
            required biometric consent and notice for this space.
          </span>
        </label>
        {error && <ErrorBox message={error} />}
        <div className="modal-actions">
          <span className="caption">
            Enable live matching in Manage camera after testing.
          </span>
          <ModalCancel disabled={busy} />
          <Button type="submit" busy={busy}>
            <Video size={16} />
            Connect & start monitoring
          </Button>
        </div>
      </form>
    </Modal>
  );
}
