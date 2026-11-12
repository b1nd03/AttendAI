import { useCallback, useEffect, useRef, useState } from "react";
const photoConstraints: MediaTrackConstraints = {
  width: { ideal: 1280 },
  height: { ideal: 960 },
  facingMode: "user",
};

export function useCamera(constraints = photoConstraints) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const mounted = useRef(true);
  const attempt = useRef(0);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [sourceSize, setSourceSize] = useState<[number, number]>();
  const stop = useCallback(() => {
    attempt.current++;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setActive(false);
    setStarting(false);
    setSourceSize(undefined);
  }, []);
  const start = useCallback(
    async (requested = constraints) => {
      stop();
      const current = ++attempt.current;
      setStarting(true);
      setError("");
      try {
        if (!navigator.mediaDevices?.getUserMedia)
          throw new Error("Camera access needs localhost or HTTPS.");
        const stream = await navigator.mediaDevices.getUserMedia({
          video: requested,
          audio: false,
        });
        if (!mounted.current || current !== attempt.current) {
          stream.getTracks().forEach((t) => t.stop());
          return false;
        }
        streamRef.current = stream;
        stream.getVideoTracks().forEach((track) =>
          track.addEventListener(
            "ended",
            () => {
              if (streamRef.current === stream) {
                stop();
                setError(
                  "Camera access ended. Share the camera again to reconnect.",
                );
              }
            },
            { once: true },
          ),
        );
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
        }
        if (!mounted.current || current !== attempt.current) return false;
        const settings = stream.getVideoTracks()[0]?.getSettings();
        const width = settings?.width || videoRef.current?.videoWidth;
        const height = settings?.height || videoRef.current?.videoHeight;
        if (width && height) setSourceSize([width, height]);
        setActive(true);
        return true;
      } catch (e) {
        if (!mounted.current || current !== attempt.current) return false;
        stop();
        setError(
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Camera permission was denied. Allow access in your browser settings and retry."
            : e instanceof DOMException && e.name === "NotFoundError"
              ? "No camera found. Connect a webcam, then retry."
              : e instanceof DOMException && e.name === "NotReadableError"
                ? "The camera is busy. Close other camera apps and try again."
                : e instanceof Error
                  ? e.message
                  : "Cannot open the camera.",
        );
        return false;
      } finally {
        if (mounted.current && current === attempt.current) setStarting(false);
      }
    },
    [constraints, stop],
  );
  const capture = useCallback(
    async (quality = 0.9, maxEdge = 960, maxPixels = Infinity) => {
      const video = videoRef.current;
      if (!video || video.readyState < 2 || !video.videoWidth)
        throw new Error("Camera is warming up. Try again in a moment.");
      const canvas = (canvasRef.current ??= document.createElement("canvas"));
      const scale = Math.min(
        1,
        maxEdge / Math.max(video.videoWidth, video.videoHeight),
        Math.sqrt(maxPixels / (video.videoWidth * video.videoHeight)),
      );
      const width = Math.max(1, Math.floor(video.videoWidth * scale));
      const height = Math.max(1, Math.floor(video.videoHeight * scale));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      canvas
        .getContext("2d")!
        .drawImage(video, 0, 0, canvas.width, canvas.height);
      return await new Promise<Blob>((resolve, reject) =>
        canvas.toBlob(
          (blob) =>
            blob
              ? resolve(blob)
              : reject(new Error("Could not capture image.")),
          "image/jpeg",
          quality,
        ),
      );
    },
    [],
  );
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);
  return {
    videoRef,
    active,
    starting,
    error,
    sourceSize,
    start,
    stop,
    capture,
    clearError: () => setError(""),
  };
}
