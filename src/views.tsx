import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Camera,
  CameraOff,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  Fingerprint,
  Focus,
  GitBranch,
  Layers3,
  LockKeyhole,
  MoreHorizontal,
  Play,
  Plus,
  RotateCcw,
  ScanFace,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  UserCheck,
  Users,
  UserRoundPlus,
  Workflow,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, dateLabel, errorMessage, json, localDay, timeLabel } from "./api";
import {
  Avatar,
  Badge,
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  PageHeading,
  Pagination,
  SearchField,
  useToast,
} from "./components";
import type { LaptopCamera } from "./laptop-camera";
import type {
  Attendance,
  Audit,
  Cluster,
  Dashboard,
  Page,
  ManualAttendanceTarget,
  Person,
  Settings,
} from "./types";

export interface ViewProps {
  data: Dashboard;
  refresh: () => void;
  onEnroll: () => void;
  onManual: (target?: ManualAttendanceTarget) => void;
  onPerson: (person: Person) => void;
  navigate: (page: Page, group?: string) => void;
  selectedGroup?: string;
  laptop: LaptopCamera;
}
function PanelTitle({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="panel-title">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
export function AttendanceTable({
  events,
  zone,
  onPerson,
  people,
  compact = false,
  showScores = false,
}: {
  events: Attendance[];
  zone: string;
  onPerson: (p: Person) => void;
  people: Person[];
  compact?: boolean;
  showScores?: boolean;
}) {
  return (
    <div className="table-scroll">
      <table aria-label="Attendance records">
        <thead>
          <tr>
            <th scope="col">Person</th>
            <th scope="col">{compact ? "Time" : "Event"}</th>
            <th scope="col">Verification</th>
            {!compact && <th scope="col">Department</th>}
            {(compact || showScores) && (
              <th scope="col">{compact ? "Status" : "Match score"}</th>
            )}
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td>
                <button
                  className="person-button"
                  onClick={() => {
                    const person = people.find((p) => p.id === event.person_id);
                    if (person) onPerson(person);
                  }}
                >
                  <Avatar name={event.name} />
                  <span>
                    <strong>{event.name}</strong>
                    <small>{event.code}</small>
                  </span>
                </button>
              </td>
              <td>
                <span className="event-time">
                  {timeLabel(event.created_at, zone)}
                </span>
                {!compact && (
                  <span
                    className={`event-kind ${event.kind === "out" ? "event-out" : ""}`}
                  >
                    {event.kind === "in" ? (
                      <ArrowDownLeft size={13} />
                    ) : (
                      <ArrowUpRight size={13} />
                    )}
                    Check-{event.kind}
                  </span>
                )}
              </td>
              <td>
                <span className="verification">
                  {event.method !== "manual" ? (
                    <ScanFace size={15} />
                  ) : (
                    <UserCheck size={15} />
                  )}{" "}
                  {event.method === "manual" ? "Manual entry" : "Face verified"}
                </span>
              </td>
              {!compact && <td>{event.department}</td>}
              {(compact || showScores) && (
                <td>
                  {compact ? (
                    <Badge tone={event.kind === "in" ? "success" : "neutral"}>
                      {event.kind === "in" ? "Checked in" : "Checked out"}
                    </Badge>
                  ) : event.score !== null ? (
                    <span className="mono">{event.score.toFixed(3)}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Overview(props: ViewProps) {
  const { data, navigate, onEnroll, onPerson } = props;
  const { stats } = data;
  const percent = stats.enrolled
    ? Math.round((stats.present / stats.enrolled) * 100)
    : 0;
  const hours = Array.from({ length: 11 }, (_, i) => i + 6);
  const max = Math.max(1, ...data.hourly);
  const chartHours = hours.map((h) => ({ hour: h, count: data.hourly[h] }));
  return (
    <>
      <PageHeading
        section="Daily summary"
        title="Attendance overview"
        description="See who has checked in today and what needs your attention."
        actions={
          <Button onClick={onEnroll}>
            <Plus size={17} />
            Enroll a person
          </Button>
        }
      />
      <div className="stats-grid">
        {[
          {
            label: "Present today",
            value: stats.present,
            icon: UserCheck,
            tone: "blue",
            footer: `${percent}% of your people`,
            extra: "Checked in",
          },
          {
            label: "People enrolled",
            value: stats.enrolled,
            icon: Users,
            tone: "purple",
            footer: "Ready for face attendance",
            extra: "In directory",
          },
          {
            label: "Not checked in",
            value: stats.absent,
            icon: Clock3,
            tone: "amber",
            footer: "No check-in recorded today",
            extra: "Today",
          },
          {
            label: "Awaiting review",
            value: stats.review,
            icon: Layers3,
            tone: "teal",
            footer: "Unknown faces to review",
            extra: stats.review ? "Needs attention" : "All clear",
          },
        ].map(({ label, value, icon: Icon, tone, footer, extra }) => (
          <div className="stat-card" key={label}>
            <div className="stat-top">
              <span>{label}</span>
              <span className={`stat-icon ${tone}`}>
                <Icon size={19} />
              </span>
            </div>
            <div className="stat-value">
              {value}
              <span>{extra}</span>
            </div>
            <div className="stat-foot">
              {label === "Present today" ? (
                <span className="mini-progress">
                  <i style={{ width: `${percent}%` }} />
                </span>
              ) : (
                <span className={`mini-dot ${tone}`} />
              )}
              <span>{footer}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="overview-grid">
        <div className="overview-main">
          <section className="panel chart-panel">
            <PanelTitle
              title="Check-ins by hour"
              subtitle="Check-ins by hour · your local time"
              action={<Badge tone="blue">{dateLabel(data.day)}</Badge>}
            />
            <div className="chart-legend">
              <span className="legend-dot" />
              Check-ins
              <span className="chart-total">
                {stats.present} people checked in
              </span>
            </div>
            <div
              className="attendance-chart"
              role="img"
              aria-label={`Hourly check-ins: ${chartHours.map((x) => `${x.hour}:00, ${x.count}`).join("; ")}`}
            >
              <div className="chart-y">
                {[max, Math.round(max / 2), 0].map((v, i) => (
                  <span key={i}>{v}</span>
                ))}
              </div>
              <div className="chart-bars">
                <div className="chart-grid">
                  <i />
                  <i />
                  <i />
                </div>
                {chartHours.map(({ hour, count }) => (
                  <div className="chart-column" key={hour}>
                    <div
                      className={`chart-bar ${count === max ? "chart-highlight" : ""}`}
                      style={{
                        height: count
                          ? `${Math.max(5, (count / max) * 100)}%`
                          : "3px",
                      }}
                    >
                      <span>{count} check-ins</span>
                    </div>
                    <span className="chart-x">
                      {String(hour).padStart(2, "0")}:00
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="chart-note">
              <Clock3 size={14} />
              Hourly bars include every check-in. The summary counts each person
              once.
              <span>{data.settings.timezone.replace("_", " ")}</span>
            </div>
          </section>
          <section className="panel recent-panel">
            <PanelTitle
              title="Recent attendance"
              subtitle="Familiar faces, a simpler morning."
              action={
                <button
                  className="text-link"
                  onClick={() => navigate("attendance")}
                >
                  View all records
                  <ArrowRight size={15} />
                </button>
              }
            />
            {data.events.length ? (
              <AttendanceTable
                events={data.events.slice(0, 5)}
                zone={data.settings.timezone}
                onPerson={onPerson}
                people={data.people}
                compact
              />
            ) : (
              <Empty
                title="Your first hello starts here"
                description="Enroll a person, then open the camera to record your first check-in."
                action={
                  <Button variant="secondary" onClick={onEnroll}>
                    <UserRoundPlus size={16} />
                    Enroll a person
                  </Button>
                }
              />
            )}
            <div className="panel-footer">
              <ShieldCheck size={14} />
              Attendance is saved locally.
            </div>
          </section>
        </div>
        <div className="overview-aside">
          <section className="panel work-links" aria-label="Daily tasks">
            <h2>Daily tasks</h2>
            <Button variant="secondary" onClick={() => navigate("scanner")}>
              <Camera size={18} /> Open Live Monitor <ArrowRight size={16} />
            </Button>
            <Button variant="secondary" onClick={() => navigate("review")}>
              <ScanFace size={18} /> Review faces <ArrowRight size={16} />
            </Button>
            <Button variant="secondary" onClick={() => navigate("attendance")}>
              <UserCheck size={18} /> View attendance <ArrowRight size={16} />
            </Button>
          </section>
        </div>
      </div>
    </>
  );
}

export function People(props: ViewProps) {
  const { onEnroll, onPerson } = props;
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<Person[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError("");
    const timer = setTimeout(
      () => {
        api<{ items: Person[]; total: number; page: number }>(
          `/people?q=${encodeURIComponent(q)}&page=${page}`,
          { signal: controller.signal },
        )
          .then((r) => {
            if (current) {
              setItems(r.items);
              setTotal(r.total);
              if (r.page !== page) setPage(r.page);
            }
          })
          .catch((e) => {
            if (current) setError(errorMessage(e));
          })
          .finally(() => {
            if (current) setLoading(false);
          });
      },
      q ? 300 : 0,
    );
    return () => {
      current = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [q, page, retry, props.data.stats.enrolled]);
  return (
    <>
      <PageHeading
        section="Directory"
        title="People"
        description="Find someone, add a person, or manage their attendance."
        actions={
          <Button onClick={onEnroll}>
            <Plus size={17} />
            Enroll a person
          </Button>
        }
      />
      <section className="panel directory">
        <div className="directory-toolbar">
          <div>
            <h2>
              Enrolled people <Badge>{total}</Badge>
            </h2>
            <p>Open a profile to view or edit it.</p>
          </div>
          <SearchField
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
            placeholder="Search name, ID, or department"
          />
        </div>
        {error ? (
          <ErrorBox message={error} retry={() => setRetry((r) => r + 1)} />
        ) : loading ? (
          <Loading label="Loading people" />
        ) : items.length ? (
          <div className="people-grid">
            {items.map((p) => (
              <button
                className="person-card"
                key={p.id}
                onClick={() => onPerson(p)}
              >
                <div className="person-card-top">
                  <Avatar name={p.name} size="large" />
                  <ChevronRight size={16} />
                </div>
                <h3>{p.name}</h3>
                <p>
                  {p.code} <span>·</span> {p.department}
                </p>
                <div className="person-card-foot">
                  <Badge tone="success">
                    <ShieldCheck size={12} />
                    Enrolled
                  </Badge>
                  <span>
                    <Layers3 size={13} />
                    {p.templates} templates
                  </span>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <Empty
            title={
              q ? "No people match your search" : "Make your first introduction"
            }
            description={
              q
                ? "Try a different name, ID, or department."
                : "Enroll your first person with a few clear images and their consent."
            }
            action={
              q ? (
                <Button variant="secondary" onClick={() => setQ("")}>
                  Clear search
                </Button>
              ) : (
                <Button onClick={onEnroll}>
                  <Plus size={17} />
                  Enroll a person
                </Button>
              )
            }
          />
        )}
        <Pagination page={page} total={total} size={12} onChange={setPage} />
      </section>
      <div className="notice">
        <ShieldCheck size={18} />
        <p>
          Enrollment needs explicit biometric consent and an in-person identity
          check. Face images are processed locally; only encrypted templates are
          kept.
        </p>
      </div>
    </>
  );
}

export function Records({ data, onPerson, onManual }: ViewProps) {
  const [day, setDay] = useState(data.day);
  const [selected, setSelected] = useState<Dashboard>(data);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("all");
  const [showScores, setShowScores] = useState(false);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  useEffect(() => {
    setPage(1);
    if (day === data.day) {
      setBusy(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    setBusy(true);
    setError("");
    api<Dashboard>(`/dashboard?day=${day}`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setSelected(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [day, data.day]);
  const filtered = (day === data.day ? data.events : selected.events).filter(
    (e) =>
      (kind === "all" || e.kind === kind) &&
      (e.name + " " + e.code + " " + e.department)
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  const download = async () => {
    setError("");
    try {
      const res = await fetch(`/api/export?start=${day}&end=${day}`);
      if (!res.ok)
        throw new Error(
          "Export could not be completed. Sign in again or retry.",
        );
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `presence-${day}.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("Attendance CSV exported.");
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  return (
    <>
      <PageHeading
        section="Records"
        title="Attendance records"
        description="Find check-ins and check-outs by date or person."
        actions={
          <>
            <Button onClick={() => onManual()}>
              <UserCheck size={16} />
              Manual entry
            </Button>
            <Button variant="secondary" onClick={() => void download()}>
              <Download size={16} />
              Export CSV
            </Button>
          </>
        }
      />
      <section className="panel">
        <div className="records-toolbar">
          <SearchField
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
            placeholder="Search attendance"
          />
          <div>
            <label className="sr-only" htmlFor="record-kind">
              Event type
            </label>
            <select
              id="record-kind"
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">All events</option>
              <option value="in">Check-ins</option>
              <option value="out">Check-outs</option>
            </select>
            <label className="sr-only" htmlFor="record-day">
              Attendance date
            </label>
            <input
              type="date"
              id="record-day"
              value={day}
              max={localDay(data.settings.timezone)}
              onChange={(e) => {
                if (e.target.value) setDay(e.target.value);
              }}
            />
          </div>
        </div>
        <div className="records-summary">
          <span>{dateLabel(day)}</span>
          <span>
            {filtered.length} matching{" "}
            {filtered.length === 1 ? "entry" : "entries"} ·{" "}
            {data.settings.timezone}
          </span>
          <Button
            variant="ghost"
            aria-pressed={showScores}
            onClick={() => setShowScores(!showScores)}
          >
            Match scores
          </Button>
        </div>
        {error && <ErrorBox message={error} />}{" "}
        {busy ? (
          <Loading label="Loading attendance" />
        ) : filtered.length ? (
          <AttendanceTable
            events={filtered.slice((page - 1) * 10, page * 10)}
            showScores={showScores}
            zone={data.settings.timezone}
            people={data.people}
            onPerson={onPerson}
          />
        ) : (
          <Empty
            title={q ? "No matching attendance" : "No attendance on this date"}
            description={
              q
                ? "Clear your search or choose a different event type."
                : "Choose another date, or start live attendance to record a check-in."
            }
            action={
              q ? (
                <Button variant="secondary" onClick={() => setQ("")}>
                  Clear search
                </Button>
              ) : undefined
            }
          />
        )}
        <Pagination
          page={page}
          total={filtered.length}
          size={10}
          onChange={setPage}
        />
      </section>
      <p className="caption">
        The screen shows up to the latest 200 events for the selected day. CSV
        exports include all events. Similarity is a cosine score, not an
        accuracy percentage.
      </p>
    </>
  );
}

export function ActivityLog({ data }: ViewProps) {
  const [items, setItems] = useState<Audit[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true);
    api<{ items: Audit[]; total: number; page: number }>(`/audit?page=${page}`)
      .then((r) => {
        if (current) {
          setItems(r.items);
          setTotal(r.total);
          if (r.page !== page) setPage(r.page);
        }
      })
      .catch((e) => {
        if (current) setError(errorMessage(e));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [page, retry]);
  const labels: Record<string, string> = {
    attendance_recorded: "Attendance recorded",
    person_enrolled: "Person enrolled",
    template_added: "Gallery learned a new template",
    template_rolled_back: "Template rolled back",
    settings_saved: "Settings saved",
    person_forgotten: "Person forgotten",
    workspace_created: "Local workspace created",
    cluster_assigned: "Unknown face assigned",
    cluster_deleted: "Unknown face deleted",
    attendance_exported: "Attendance exported",
  };
  return (
    <>
      <PageHeading
        section="Administration"
        title="Activity log"
        description="See changes to people, cameras, and attendance."
      />
      <section className="panel">
        <PanelTitle
          title="Workspace activity"
          subtitle="Times shown in your workspace timezone"
          action={
            <Button variant="ghost" onClick={() => setRetry((x) => x + 1)}>
              <RotateCcw size={15} />
              Refresh
            </Button>
          }
        />
        {error && (
          <ErrorBox message={error} retry={() => setRetry((x) => x + 1)} />
        )}{" "}
        {loading ? (
          <Loading label="Loading activity" />
        ) : items.length ? (
          <ol className="audit-list">
            {items.map((item) => (
              <li key={item.id}>
                <div
                  className={`audit-icon ${item.action.includes("template") ? "audit-learning" : ""}`}
                >
                  {item.action.includes("template") ? (
                    <GitBranch size={19} />
                  ) : item.action.includes("person") ? (
                    <Users size={19} />
                  ) : item.action.includes("settings") ? (
                    <SlidersHorizontal size={19} />
                  ) : (
                    <CheckCircle2 size={19} />
                  )}
                </div>
                <div>
                  <strong>
                    {labels[item.action] || item.action.replaceAll("_", " ")}
                  </strong>
                  <p>{item.detail}</p>
                  <small>
                    {item.subject === "workspace"
                      ? "Workspace"
                      : `Reference ${item.subject.slice(0, 12)}`}{" "}
                    · Local administrator or camera
                  </small>
                </div>
                <time dateTime={item.created_at}>
                  {timeLabel(item.created_at, data.settings.timezone)}
                  <span>
                    {new Date(item.created_at).toLocaleDateString("en-GB", {
                      timeZone: data.settings.timezone,
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                </time>
              </li>
            ))}
          </ol>
        ) : (
          <Empty
            title="A fresh start"
            description="Your workspace activity will appear here as you enroll people and record attendance."
          />
        )}
        <Pagination page={page} total={total} size={20} onChange={setPage} />
      </section>
    </>
  );
}

export function SettingsView({ data, refresh }: ViewProps) {
  const [form, setForm] = useState<Settings>(data.settings);
  const [initial, setInitial] = useState<Settings>(data.settings);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [retry, setRetry] = useState(0);
  const toast = useToast();
  useEffect(() => {
    let current = true;
    setLoading(true);
    api<Settings>("/settings")
      .then((r) => {
        if (current) {
          setForm(r);
          setInitial(r);
          setDirty(false);
        }
      })
      .catch((e) => {
        if (current) setError(errorMessage(e));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [retry]);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("presence-dirty", { detail: dirty }));
    return () => {
      window.dispatchEvent(
        new CustomEvent("presence-dirty", { detail: false }),
      );
    };
  }, [dirty]);
  useEffect(() => {
    if (!dirty) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  };
  const save = async () => {
    setConfirm(false);
    setBusy(true);
    setError("");
    try {
      const result = await api<Settings>("/settings", json("PUT", form));
      setInitial(result);
      setForm(result);
      setDirty(false);
      refresh();
      toast("Settings saved.");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeading
        section="Administration"
        title="Settings"
        description="Manage your workspace and choose which face samples to save."
      />
      {error && (
        <ErrorBox message={error} retry={() => setRetry((x) => x + 1)} />
      )}{" "}
      {loading ? (
        <Loading label="Loading settings" />
      ) : (
        <form
          className="settings-form"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (form.update_threshold <= form.match_threshold + 0.1) {
              setError(
                "Learning threshold must be more than 0.10 above recognition threshold.",
              );
              return;
            }
            if (
              form.auto_learn !== initial.auto_learn ||
              form.retain_unknowns !== initial.retain_unknowns
            )
              setConfirm(true);
            else void save();
          }}
        >
          <section className="panel settings-section">
            <div className="settings-description">
              <span className="settings-section-icon">
                <Users size={22} />
              </span>
              <h2>Workspace</h2>
              <p>Set the name and time zone shown in your records.</p>
            </div>
            <div className="settings-fields">
              <Field label="Organization name" id="org">
                <input
                  id="org"
                  value={form.organization}
                  maxLength={80}
                  onChange={(e) => update("organization", e.target.value)}
                />
              </Field>
              <Field
                label="Workspace timezone"
                id="timezone"
                hint="Used for attendance dates, charts, and exported records."
              >
                <select
                  id="timezone"
                  value={form.timezone}
                  onChange={(e) => update("timezone", e.target.value)}
                >
                  {[
                    "Asia/Baghdad",
                    "Asia/Dubai",
                    "Europe/London",
                    "Europe/Berlin",
                    "America/New_York",
                    "America/Los_Angeles",
                    "UTC",
                  ].map((z) => (
                    <option key={z}>{z}</option>
                  ))}
                </select>
              </Field>
            </div>
          </section>
          <section className="panel settings-section">
            <div className="settings-description">
              <span className="settings-section-icon">
                <GitBranch size={22} />
              </span>
              <h2>Face review</h2>
              <p>
                Choose whether the app saves unnamed faces for you to review.
              </p>
            </div>
            <div className="settings-fields">
              <label className="toggle-row">
                <div>
                  <strong>Collect unknown faces for review</strong>
                  <span>
                    Only enable after consent and notice are arranged. Turning
                    this off deletes the queue.
                  </span>
                </div>
                <input
                  type="checkbox"
                  role="switch"
                  aria-label="Collect unknown faces for review"
                  checked={form.retain_unknowns}
                  onChange={(e) => update("retain_unknowns", e.target.checked)}
                />
              </label>
              <Field label="Keep unnamed faces for" id="retention">
                <select
                  id="retention"
                  value={form.retention_days}
                  onChange={(e) =>
                    update("retention_days", Number(e.target.value))
                  }
                >
                  {[1, 3, 7, 14, 30].map((n) => (
                    <option key={n} value={n}>
                      {n} {n === 1 ? "day" : "days"}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </section>
          <details className="panel settings-advanced">
            <summary>Advanced settings</summary>
            <section className="settings-section">
              <div className="settings-description">
                <span className="settings-section-icon">
                  <ScanFace size={22} />
                </span>
                <h3>Face matching</h3>
                <p>
                  Require clear images and consistent decisions. Scores need
                  calibration for your camera.
                </p>
                <span className="model-tag">
                  {data.engine.model} · {data.engine.device}
                  {data.engine.accuracy_model &&
                    ` · ${data.engine.accuracy_model}`}
                </span>
              </div>
              <div className="settings-fields">
                <div className="field-row">
                  <Field
                    label="Recognition threshold"
                    id="threshold"
                    hint="Cosine similarity; higher is stricter."
                  >
                    <input
                      id="threshold"
                      type="number"
                      min="0.4"
                      max="0.95"
                      step="0.01"
                      value={form.match_threshold}
                      onChange={(e) =>
                        update("match_threshold", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field
                    label="Identity margin"
                    id="margin"
                    hint="Required gap above the next person."
                  >
                    <input
                      id="margin"
                      type="number"
                      min="0.05"
                      max="0.4"
                      step="0.01"
                      value={form.match_margin}
                      onChange={(e) =>
                        update("match_margin", Number(e.target.value))
                      }
                    />
                  </Field>
                </div>
                <div className="field-row">
                  <Field label="Frames that must agree" id="votes">
                    <select
                      id="votes"
                      value={form.vote_frames}
                      onChange={(e) =>
                        update("vote_frames", Number(e.target.value))
                      }
                    >
                      {[5, 6, 7, 8, 9, 10].map((n) => (
                        <option key={n} value={n}>
                          {n} consecutive frames
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Duplicate cooldown" id="cooldown">
                    <select
                      id="cooldown"
                      value={form.cooldown_seconds}
                      onChange={(e) =>
                        update("cooldown_seconds", Number(e.target.value))
                      }
                    >
                      {[
                        [60, "1 minute"],
                        [300, "5 minutes"],
                        [600, "10 minutes"],
                        [1800, "30 minutes"],
                      ].map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <div className="fixed-rule">
                  <ShieldCheck size={17} />
                  <span>
                    Liveness is always required for automatic attendance.
                  </span>
                  <Badge tone={data.engine.liveness ? "success" : "warning"}>
                    {data.engine.warming
                      ? "Starting"
                      : data.engine.liveness
                        ? "Available"
                        : "Unavailable"}
                  </Badge>
                </div>
              </div>
            </section>
            <section className="settings-section">
              <div className="settings-description">
                <span className="settings-section-icon">
                  <GitBranch size={22} />
                </span>
                <h3>Learning from check-ins</h3>
                <p>
                  Review these settings before changing how the app updates
                  saved face samples.
                </p>
              </div>
              <div className="settings-fields">
                <label className="toggle-row">
                  <div>
                    <strong>Learn from verified check-ins</strong>
                    <span>
                      Add varied, high-quality templates after strict matching
                      and liveness checks.
                    </span>
                  </div>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="Learn from verified check-ins"
                    checked={form.auto_learn}
                    onChange={(e) => update("auto_learn", e.target.checked)}
                  />
                </label>
                <Field
                  label="Learning threshold"
                  id="update-threshold"
                  hint="Must exceed recognition by more than 0.10. Learning also needs an anchor match."
                >
                  <input
                    id="update-threshold"
                    type="number"
                    step="0.01"
                    min="0.7"
                    max="0.99"
                    value={form.update_threshold}
                    onChange={(e) =>
                      update("update_threshold", Number(e.target.value))
                    }
                  />
                </Field>
                <Field label="Templates per person" id="templates">
                  <select
                    id="templates"
                    value={form.max_templates}
                    onChange={(e) =>
                      update("max_templates", Number(e.target.value))
                    }
                  >
                    {[5, 6, 7, 8, 9, 10].map((n) => (
                      <option key={n} value={n}>
                        {n} maximum
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            </section>
          </details>
          <div
            className={`settings-actions${dirty ? " settings-actions-dirty" : ""}`}
          >
            <span>
              {dirty
                ? "You have unsaved changes."
                : "Your configuration is up to date."}
            </span>
            <Button
              variant="secondary"
              type="button"
              disabled={!dirty || busy}
              onClick={() => {
                setForm(initial);
                setDirty(false);
                setError("");
              }}
            >
              Discard changes
            </Button>
            <Button type="submit" busy={busy} disabled={!dirty}>
              Save changes
            </Button>
          </div>
        </form>
      )}
      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Apply learning and privacy changes?"
        description="Review what will change before saving."
      >
        <ul className="confirmation-list">
          {form.auto_learn !== initial.auto_learn && (
            <li>
              {form.auto_learn
                ? "Enable automatic gallery updates from strongly verified attendance."
                : "Pause automatic gallery updates."}
            </li>
          )}
          {form.retain_unknowns !== initial.retain_unknowns && (
            <li>
              {form.retain_unknowns
                ? "Enable encrypted storage of unknown face crops and templates for review. Consent and notice must be arranged first."
                : "Delete all unknown face samples and disable their collection."}
            </li>
          )}
        </ul>
        <div className="modal-actions">
          <Button
            variant="secondary"
            autoFocus
            onClick={() => setConfirm(false)}
          >
            Keep editing
          </Button>
          <Button onClick={() => void save()}>Apply changes</Button>
        </div>
      </Modal>
    </>
  );
}
