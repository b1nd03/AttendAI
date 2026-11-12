import { Camera, Monitor, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, json } from "./api";
import { Button, ErrorBox, Field, Modal } from "./components";
import { useCamera } from "./camera";

const liveConstraints: MediaTrackConstraints = {
  width: { ideal: 1920 },
  height: { ideal: 1080 },
  frameRate: { ideal: 30, max: 30 },
  facingMode: "user",
};
const detailConstraints: MediaTrackConstraints = {
  ...liveConstraints,
  width: { ideal: 3840 },
  height: { ideal: 2160 },
  frameRate: { ideal: 24, max: 30 },
};

interface Share {
  id: string;
  share_token: string;
  frame_max_edge: number;
  frame_max_pixels: number;
  frame_max_bytes: number;
  upload_fps: number;
}
interface ShareOptions {
  authorized: boolean;
  role: "in" | "observe";
  collect_unknowns: boolean;
  distance_mode: boolean;
}

function releaseShare(share: Share) {
  void fetch(`/api/cameras/${share.id}/browser-stop`, {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
    headers: { "X-Camera-Share": share.share_token },
  }).catch(() => {});
}

export function useLaptopCamera() {
  const {
    videoRef,
    active,
    starting,
    error: deviceError,
    start: openDevice,
    stop: closeDevice,
    capture,
    sourceSize,
    clearError: clearDeviceError,
  } = useCamera(liveConstraints);
  const [share, setShare] = useState<Share | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const current = useRef<Share | null>(null);
  const generation = useRef(0);

  const stop = useCallback(() => {
    generation.current++;
    closeDevice();
    if (current.current) releaseShare(current.current);
    current.current = null;
    setShare(null);
    setConnecting(false);
  }, [closeDevice]);

  const start = useCallback(
    async (options: ShareOptions) => {
      setError("");
      const requested = ++generation.current;
      setConnecting(true);
      try {
        if (
          !(await openDevice(
            options.distance_mode ? detailConstraints : liveConstraints,
          )) ||
          requested !== generation.current
        )
          return false;
        const next = await api<Share>(
          "/cameras/browser",
          json("POST", options),
        );
        if (requested !== generation.current) {
          releaseShare(next);
          return false;
        }
        current.current = next;
        setShare(next);
        return true;
      } catch (e) {
        if (requested === generation.current) {
          closeDevice();
          setError(errorMessage(e));
        }
        return false;
      } finally {
        if (requested === generation.current) setConnecting(false);
      }
    },
    [openDevice, closeDevice],
  );

  useEffect(() => {
    if (!share) return;
    if (!active) {
      stop();
      return;
    }
    const controller = new AbortController();
    const { frame_max_edge, frame_max_pixels, frame_max_bytes, upload_fps } =
      share;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function pushFrame() {
      const started = performance.now();
      try {
        let image = await capture(0.86, frame_max_edge, frame_max_pixels);
        if (image.size > frame_max_bytes && !controller.signal.aborted)
          image = await capture(0.7, frame_max_edge, frame_max_pixels);
        if (image.size > frame_max_bytes && !controller.signal.aborted)
          image = await capture(0.58, frame_max_edge, frame_max_pixels);
        if (image.size > frame_max_bytes && !controller.signal.aborted)
          image = await capture(
            0.65,
            Math.floor(frame_max_edge * 0.8),
            frame_max_pixels,
          );
        if (controller.signal.aborted) return;
        if (image.size > frame_max_bytes)
          throw new Error(
            "Camera frames are too large to share. Try standard detail.",
          );
        const body = new FormData();
        body.append("file", image, "camera.jpg");
        await api(`/cameras/${share!.id}/browser-frame`, {
          method: "POST",
          body,
          headers: { "X-Camera-Share": share!.share_token },
          signal: AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(5500),
          ]),
        });
        failures = 0;
      } catch (e) {
        if (controller.signal.aborted) return;
        if (
          ++failures >= 3 ||
          (e instanceof ApiError && [401, 410].includes(e.status))
        ) {
          stop();
          setError(errorMessage(e));
          return;
        }
      }
      if (!controller.signal.aborted) {
        timer = setTimeout(
          pushFrame,
          Math.max(40, 1000 / upload_fps - (performance.now() - started)),
        );
      }
    }
    void pushFrame();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, share, capture, stop]);

  useEffect(() => {
    window.addEventListener("pagehide", stop);
    return () => {
      window.removeEventListener("pagehide", stop);
      if (current.current) releaseShare(current.current);
      current.current = null;
      generation.current++;
    };
  }, [stop]);

  return {
    videoRef,
    sourceSize,
    shareId: share?.id,
    busy: connecting || starting,
    error: error || deviceError,
    start,
    stop,
    clearError: () => {
      setError("");
      clearDeviceError();
    },
  };
}

export type LaptopCamera = ReturnType<typeof useLaptopCamera>;

export function LaptopCameraDialog({
  open,
  camera,
  collecting,
  onClose,
  onShared,
  onDirect,
}: {
  open: boolean;
  camera: LaptopCamera;
  collecting: boolean;
  onClose: () => void;
  onShared: () => void;
  onDirect: () => void;
}) {
  const [authorized, setAuthorized] = useState(false);
  const [collect, setCollect] = useState(false);
  const [role, setRole] = useState<"in" | "observe">("in");
  const [distance, setDistance] = useState(false);
  const [validation, setValidation] = useState("");
  useEffect(() => {
    if (open) {
      setAuthorized(false);
      setCollect(false);
      setRole("in");
      setDistance(false);
      setValidation("");
    }
  }, [open]);
  function cancel() {
    if (camera.busy) camera.stop();
    onClose();
  }
  return (
    <Modal
      open={open}
      onClose={cancel}
      title="Share your laptop camera"
      description="Allow camera access when your browser asks. Keep this tab open while you share."
    >
      <form
        noValidate
        onSubmit={async (e) => {
          e.preventDefault();
          if (!authorized) {
            setValidation(
              "Confirm that you are authorized to use this camera.",
            );
            document.getElementById("laptop-authorized")?.focus();
            return;
          }
          setValidation("");
          if (
            await camera.start({
              authorized,
              role,
              collect_unknowns: collect,
              distance_mode: distance,
            })
          )
            onShared();
        }}
      >
        <Field label="Camera purpose" id="laptop-role">
          <select
            id="laptop-role"
            value={role}
            disabled={camera.busy}
            onChange={(e) => setRole(e.target.value as "in" | "observe")}
          >
            <option value="in">Record verified check-ins</option>
            <option value="observe">Log sightings only</option>
          </select>
        </Field>
        <details className="camera-advanced">
          <summary>Advanced camera settings</summary>
          <label className="check-field">
            <input
              type="checkbox"
              checked={distance}
              disabled={camera.busy}
              onChange={(e) => setDistance(e.target.checked)}
            />
            <span>
              <strong>High detail for distant faces</strong>
              <br />
              Use up to 4K when the webcam supports it. Analysis runs at a lower
              frame rate.
            </span>
          </label>
        </details>
        <label className="check-field">
          <input
            id="laptop-authorized"
            type="checkbox"
            checked={authorized}
            onChange={(e) => setAuthorized(e.target.checked)}
            disabled={camera.busy}
          />
          <span>
            I’m authorized to use this camera and have arranged notice and
            consent for people in view.
          </span>
        </label>
        {collecting ? (
          <p className="attend-muted">
            This workspace is already saving unknown faces. You can change that
            in Settings.
          </p>
        ) : (
          <label className="check-field">
            <input
              type="checkbox"
              checked={collect}
              onChange={(e) => setCollect(e.target.checked)}
              disabled={camera.busy}
            />
            <span>
              Save unknown faces in this workspace so I can name them later
            </span>
          </label>
        )}
        <p className="camera-share-note">
          <ShieldCheck size={16} />
          Frames are analyzed on this computer. Audio stays off.
        </p>
        {(validation || camera.error) && (
          <ErrorBox message={validation || camera.error} />
        )}
        {camera.busy && (
          <p role="status">
            Waiting for the camera. Check your browser’s permission prompt.
          </p>
        )}
        <div className="modal-actions">
          <Button type="button" variant="secondary" onClick={cancel}>
            Cancel
          </Button>
          <Button type="submit" busy={camera.busy}>
            <Camera size={16} />
            Allow and share
          </Button>
        </div>
        <div className="camera-direct-option">
          <Button
            type="button"
            variant="ghost"
            disabled={camera.busy}
            onClick={onDirect}
          >
            <Monitor size={16} />
            Connect through Windows instead
          </Button>
        </div>
      </form>
    </Modal>
  );
}
