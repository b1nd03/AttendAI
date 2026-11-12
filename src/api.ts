export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`/api${path}`, {
      ...options,
      signal: options.signal
        ? AbortSignal.any([options.signal, controller.signal])
        : controller.signal,
      credentials: "same-origin",
      headers:
        options.body instanceof FormData
          ? options.headers
          : { "Content-Type": "application/json", ...options.headers },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const message =
        typeof body?.detail === "string"
          ? body.detail
          : response.status === 422
            ? "Check the entered values and try again."
            : `Request could not be completed (${response.status}). Try again.`;
      if (response.status === 401)
        window.dispatchEvent(new Event("presence-session-expired"));
      throw new ApiError(message, response.status);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError")
      throw new Error(
        "Request timed out or was cancelled. Check the connection and retry.",
      );
    throw new Error(
      "Cannot reach the local server. Start the app, then retry.",
    );
  } finally {
    clearTimeout(timeout);
  }
}
export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});
export const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : "Something went wrong. Try again.";
export function localDay(zone = "Asia/Baghdad") {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
export function timeLabel(value: string, zone = "Asia/Baghdad") {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
export function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value + "T12:00:00Z"));
}
export function preciseTime(value: string, zone: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
