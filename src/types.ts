export interface Person {
  id: string;
  name: string;
  code: string;
  department: string;
  templates: number;
  accuracy_templates?: number;
  attendance_enabled?: number;
  consent_at: string;
  created_at: string;
}
export interface Attendance {
  id: string;
  person_id: string;
  name: string;
  code: string;
  department: string;
  kind: "in" | "out";
  method: string;
  score: number | null;
  note: string;
  created_at: string;
}
export interface Engine {
  warming?: boolean;
  accuracy_model?: string | null;
  recognition: boolean;
  liveness: boolean;
  model: string;
  dimensions: number;
  device: string;
  error: string | null;
}
export interface Settings {
  organization: string;
  timezone: string;
  match_threshold: number;
  update_threshold: number;
  match_margin: number;
  liveness_threshold: number;
  min_face_size: number;
  min_blur: number;
  vote_frames: number;
  cooldown_seconds: number;
  auto_learn: boolean;
  retain_unknowns: boolean;
  retention_days: number;
  max_templates: number;
  revision?: number;
}
export interface Dashboard {
  people: Person[];
  events: Attendance[];
  day: string;
  hourly: number[];
  stats: {
    enrolled: number;
    present: number;
    inside: number;
    absent: number;
    review: number;
    templates: number;
  };
  settings: Settings;
  engine: Engine;
}
export interface Audit {
  id: string;
  action: string;
  subject: string;
  detail: string;
  created_at: string;
}
export interface Cluster {
  id: string;
  sample: string;
  sightings: number;
  first_seen: string;
  last_seen: string;
  samples?: string[];
  qualities?: number[];
  sample_count?: number;
  camera_name?: string | null;
  observations?: {
    camera_name: string;
    created_at: string;
    liveness: number | null;
  }[];
}
export type CameraMode = "live" | "setup" | "preview";

export interface CameraSource {
  id: string;
  name: string;
  location: string;
  source_type: "network" | "usb" | "browser";
  source_hint: string;
  role: "in" | "out" | "observe" | "entry_exit";
  trigger_mode: "appearance" | "line";
  line_axis: "x" | "y";
  line_position: number;
  direction: number;
  analysis_fps: number;
  enabled: boolean;
  state: string;
  error: string;
  latency_ms: number;
  processed_fps: number;
  frame_age_seconds: number | null;
  analysis_size?: [number, number] | null;
  source_size?: [number, number] | null;
  analysis_state?:
    "waiting" | "starting" | "current" | "delayed" | "error" | "paused";
  distance_mode: boolean;
  operation_mode?: CameraMode;
  setup_test?: {
    frames: number;
    face_frames: number;
    usable_frames: number;
    largest_face_pixels: number;
    required_face_pixels: number;
    latest_blocker: string | null;
  } | null;
  people_count: number;
  preview_ready: boolean;
  tracks: {
    box: number[];
    label: string;
    state: string;
    track: string;
    person_id?: string;
    score?: number;
    votes?: number;
    needed?: number;
    message?: string;
    face_pixels?: number;
    required_face_pixels?: number;
    review_status?: "collecting" | "ready" | "dismissed" | "full" | "off";
    review_samples?: number;
    review_group_id?: string;
    manual_kind?: "in" | "out";
  }[];
}
export interface ManualAttendanceTarget {
  personId: string;
  kind: "in" | "out";
}
export interface Sighting {
  id: string;
  camera_id: string | null;
  camera_name: string;
  person_id: string | null;
  name: string | null;
  code: string | null;
  location: string;
  track_id: string;
  state: string;
  score: number | null;
  liveness: number | null;
  attendance_kind: "in" | "out" | null;
  learned: number;
  created_at: string;
}
export interface Template {
  id: string;
  source: string;
  active: number;
  quality: number;
  created_at: string;
}
export interface ScanResult {
  state: string;
  message: string;
  name?: string;
  score?: number;
  liveness?: number | null;
  quality?: number;
  votes: number;
  needed: number;
  latency_ms?: number;
  learned?: boolean;
  box?: number[];
  width?: number;
  height?: number;
}
export type Page =
  | "overview"
  | "scanner"
  | "attendance"
  | "people"
  | "review"
  | "activity"
  | "settings";
