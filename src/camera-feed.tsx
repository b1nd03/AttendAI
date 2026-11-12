import { memo, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

export const LaptopPreview = memo(function LaptopPreview({
  source,
}: {
  source: RefObject<HTMLVideoElement | null>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const player = video.current;
    if (!player) return;
    player.srcObject = source.current?.srcObject ?? null;
    return () => {
      player.srcObject = null;
    };
  }, [source]);
  return (
    <video
      ref={video}
      className="attend-feed-image"
      aria-label="Live video from Laptop camera"
      autoPlay
      playsInline
      muted
    />
  );
});

export const CameraPreview = memo(function CameraPreview({
  id,
  name,
  analyzed,
}: {
  id: string;
  name: string;
  analyzed: boolean;
}) {
  const image = useRef<HTMLImageElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const player = image.current;
    if (!player) return;
    const target = player;
    const controller = new AbortController();
    let request: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let visible = false;
    let previous: string | null = null;
    let displayed: string | null = null;
    const url = `/api/cameras/${id}/snapshot${analyzed ? "" : "?live=true"}`;
    const shouldPlay = () =>
      visible && !document.hidden && !controller.signal.aborted;
    const clearImage = () => {
      if (displayed) URL.revokeObjectURL(displayed);
      displayed = null;
      previous = null;
      target.removeAttribute("src");
    };

    async function pull() {
      if (!shouldPlay() || request) return;
      request = new AbortController();
      const active = request;
      const start = performance.now();
      let next: string | undefined;
      try {
        const response = await fetch(url, {
          headers: previous ? { "If-None-Match": previous } : {},
          cache: "no-store",
          signal: AbortSignal.any([active.signal, AbortSignal.timeout(3000)]),
        });
        if (response.status === 401) {
          window.dispatchEvent(new Event("presence-session-expired"));
        } else if (response.status === 404 && !active.signal.aborted) {
          clearImage();
          setReady(false);
        } else if (response.ok) {
          next = URL.createObjectURL(await response.blob());
          const decoded = new Image();
          decoded.src = next;
          await decoded.decode();
          if (!shouldPlay() || active.signal.aborted) return;
          if (displayed) URL.revokeObjectURL(displayed);
          target.src = next;
          displayed = next;
          next = undefined;
          previous = response.headers.get("ETag");
          setReady(true);
        }
      } catch {
        // Keep the last good frame through transient network and decode errors.
      } finally {
        if (next) URL.revokeObjectURL(next);
        request = undefined;
        if (shouldPlay()) {
          timer = setTimeout(
            pull,
            Math.max(30, 180 - (performance.now() - start)),
          );
        }
      }
    }

    const update = () => {
      clearTimeout(timer);
      if (shouldPlay()) void pull();
      else request?.abort();
    };
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    });
    observer.observe(target);
    document.addEventListener("visibilitychange", update);
    return () => {
      controller.abort();
      request?.abort();
      clearTimeout(timer);
      observer.disconnect();
      document.removeEventListener("visibilitychange", update);
      clearImage();
    };
  }, [id, analyzed]);
  return (
    <>
      {!ready && (
        <div className="attend-feed-empty">
          {analyzed ? "Waiting for an analyzed frame" : "Opening live preview"}
        </div>
      )}
      <img
        ref={image}
        className="attend-feed-image"
        style={ready ? undefined : { opacity: 0 }}
        alt={`${analyzed ? "Analyzed live frame" : "Live preview"} from ${name}`}
      />
    </>
  );
});
