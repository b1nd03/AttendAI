import {
  Activity,
  Camera,
  CircleHelp,
  ClipboardCheck,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Menu,
  ScanFace,
  Settings,
  Users,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api, errorMessage, json } from "./api";
import { Button, ErrorBox, Loading, Modal, useToast } from "./components";
import {
  WorkspaceAccess,
  EnrollmentDialog,
  ManualDialog,
  PersonDialog,
} from "./dialogs";
import type { ViewProps } from "./views";
import { useLaptopCamera } from "./laptop-camera";
import type { Dashboard, ManualAttendanceTarget, Page, Person } from "./types";

const navigation = [
  ["scanner", "Live Monitor", Camera],
  ["review", "Name People", GitBranch],
  ["people", "People", Users],
  ["attendance", "Attendance", ClipboardCheck],
  ["overview", "Overview", LayoutDashboard],
  ["activity", "Activity log", Activity],
  ["settings", "Settings", Settings],
] as const;
const navGroups = [
  { label: "Daily work", links: navigation.slice(0, 5) },
  { label: "Administration", links: navigation.slice(5) },
];
const views: Record<Page, React.ComponentType<ViewProps>> = {
  overview: lazy(() =>
    import("./views").then(({ Overview }) => ({ default: Overview })),
  ),
  scanner: lazy(() =>
    import("./monitoring").then(({ CameraMonitor }) => ({
      default: CameraMonitor,
    })),
  ),
  attendance: lazy(() =>
    import("./views").then(({ Records }) => ({ default: Records })),
  ),
  people: lazy(() =>
    import("./views").then(({ People }) => ({ default: People })),
  ),
  review: lazy(() =>
    import("./naming").then(({ FaceInbox }) => ({ default: FaceInbox })),
  ),
  activity: lazy(() =>
    import("./views").then(({ ActivityLog }) => ({ default: ActivityLog })),
  ),
  settings: lazy(() =>
    import("./views").then(({ SettingsView }) => ({ default: SettingsView })),
  ),
};
const pageFromHash = (): Page =>
  navigation.find(([key]) => location.hash === `#${key}`)?.[0] ?? "scanner";

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [selectedGroup, setSelectedGroup] = useState<string>();
  const [access, setAccess] = useState<
    "checking" | "setup" | "login" | "workspace" | "offline"
  >("checking");
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [enroll, setEnroll] = useState(false);
  const [refreshPerson, setRefreshPerson] = useState<Person | null>(null);
  const [manual, setManual] = useState(false);
  const [manualTarget, setManualTarget] = useState<ManualAttendanceTarget>();
  const [person, setPerson] = useState<Person | null>(null);
  const [help, setHelp] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [discardPage, setDiscardPage] = useState<Page | null>(null);
  const [logout, setLogout] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const dirty = useRef(false);
  const requestId = useRef(0);
  const main = useRef<HTMLElement>(null);
  const toast = useToast();
  const laptop = useLaptopCamera();
  const stopLaptop = laptop.stop;

  const checkAccess = useCallback(async (signal?: AbortSignal) => {
    setAccess("checking");
    setError("");
    try {
      const health = await api<{ configured: boolean; authenticated: boolean }>(
        "/health",
        { signal },
      );
      if (signal?.aborted) return;
      if (!health.configured) {
        setAccess("setup");
        return;
      }
      setAccess(health.authenticated ? "workspace" : "login");
    } catch (e) {
      if (!signal?.aborted) {
        setError(errorMessage(e));
        setAccess("offline");
      }
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void checkAccess(controller.signal);
    return () => controller.abort();
  }, [checkAccess]);

  const refresh = useCallback(async () => {
    const id = ++requestId.current;
    try {
      const next = await api<Dashboard>("/dashboard");
      if (id === requestId.current) {
        setData(next);
        setError("");
      }
    } catch (e) {
      if (id === requestId.current) setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    if (access !== "workspace") return;
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 15000);
    return () => {
      clearInterval(timer);
      requestId.current++;
    };
  }, [access, refresh]);
  const modelsStarting = data?.engine.warming;
  useEffect(() => {
    if (access !== "workspace" || !modelsStarting) return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [access, modelsStarting, refresh]);

  useEffect(() => {
    function expired() {
      if (access === "workspace") {
        stopLaptop();
        setData(null);
        setRefreshPerson(null);
        setAccess("login");
        setError("");
      }
    }
    function markDirty(event: Event) {
      dirty.current = (event as CustomEvent<boolean>).detail;
    }
    window.addEventListener("presence-session-expired", expired);
    window.addEventListener("presence-dirty", markDirty);
    return () => {
      window.removeEventListener("presence-session-expired", expired);
      window.removeEventListener("presence-dirty", markDirty);
    };
  }, [access, stopLaptop]);

  function navigate(next: Page, group?: string) {
    setMobile(false);
    if (dirty.current && next !== page) {
      setDiscardPage(next);
      return;
    }
    if (next === "review") setSelectedGroup(group);
    location.hash = next;
  }
  useEffect(() => {
    function change() {
      const next = pageFromHash();
      if (dirty.current && next !== page) {
        history.replaceState(null, "", `#${page}`);
        setDiscardPage(next);
        return;
      }
      setPage(next);
      setMobile(false);
      window.scrollTo(0, 0);
      requestAnimationFrame(() => main.current?.focus());
    }
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, [page]);
  const title = navigation.find(([key]) => key === page)?.[1] ?? "Live Monitor";
  useEffect(() => {
    document.title =
      access === "workspace" ? `${title} — AttendAI` : "AttendAI";
  }, [access, title]);

  if (access === "checking")
    return (
      <main className="workspace-access">
        <Loading label="Opening AttendAI" />
      </main>
    );
  if (access === "offline")
    return (
      <main className="workspace-access">
        <section className="workspace-access-card">
          <h1>AttendAI is offline</h1>
          <p>Start the local server, then try again.</p>
          <ErrorBox message={error} />
          <Button onClick={() => void checkAccess()}>Try again</Button>
        </section>
      </main>
    );
  if (access === "setup" || access === "login")
    return (
      <WorkspaceAccess
        key={access}
        configured={access === "login"}
        onConflict={() => void checkAccess()}
        onSuccess={() => {
          setError("");
          setAccess("workspace");
        }}
      />
    );

  const View = views[page];
  const navContent = (
    <>
      <a
        href="#scanner"
        className="brand"
        onClick={(e) => {
          e.preventDefault();
          navigate("scanner");
        }}
      >
        <span className="brand-symbol" aria-hidden="true">
          <ScanFace size={23} />
        </span>
        <span>
          Attend<span className="brand-dot">AI</span>
          <small className="brand-workspace">
            {data?.settings.organization ?? "Attendance workspace"}
          </small>
        </span>
      </a>
      <nav aria-label="Main navigation">
        {navGroups.map(({ label: group, links }) => (
          <div className="nav-group" key={group}>
            <p className="nav-group-label">{group}</p>
            {links.map(([key, label, Icon]) => (
              <a
                href={`#${key}`}
                key={key}
                aria-current={key === page ? "page" : undefined}
                aria-label={label}
                className={`nav-link${key === page ? " active" : ""}`}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(key);
                }}
              >
                <Icon size={17} />
                <span>{label}</span>
                {key === "review" && !!data?.stats.review && (
                  <span className="nav-count" aria-hidden="true">
                    {data.stats.review}
                  </span>
                )}
              </a>
            ))}
          </div>
        ))}
      </nav>
      <div className="workspace-nav-footer">
        <button
          type="button"
          onClick={() => {
            setMobile(false);
            setHelp(true);
          }}
        >
          <CircleHelp size={16} />
          Help
        </button>
        <button
          type="button"
          onClick={() => {
            setMobile(false);
            setLogout(true);
          }}
        >
          <LogOut size={16} />
          Sign out
        </button>
      </div>
    </>
  );

  return (
    <div className={`app-shell route-${page}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          main.current?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="sidebar-content">{navContent}</div>
        <div id="route-sidebar" />
      </aside>
      <div className="app-body">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            onClick={() => setMobile(true)}
          >
            <Menu size={22} />
          </button>
          <strong className="topbar-brand">
            <ScanFace size={21} aria-hidden="true" />
            <span>
              Attend<span className="brand-dot">AI</span>
            </span>
          </strong>
          <button
            className="icon-button"
            aria-label="Help"
            onClick={() => setHelp(true)}
          >
            <CircleHelp size={20} />
          </button>
        </header>
        {laptop.shareId && (
          <div className="camera-sharing-bar" role="status">
            <span>
              <span className="tiny-dot" />
              Laptop camera is sharing
            </span>
            <Button variant="secondary" onClick={stopLaptop}>
              Stop sharing
            </Button>
          </div>
        )}
        <main
          id="main-content"
          ref={main}
          tabIndex={-1}
          className="main-content"
        >
          {error && <ErrorBox message={error} retry={() => void refresh()} />}
          {data ? (
            <Suspense fallback={<Loading label={`Opening ${title}`} />}>
              <View
                key={page}
                data={data}
                refresh={() => void refresh()}
                onEnroll={() => setEnroll(true)}
                onManual={(target) => {
                  setManualTarget(target);
                  setManual(true);
                }}
                onPerson={(p) =>
                  setPerson(data.people.find((item) => item.id === p.id) ?? p)
                }
                navigate={navigate}
                selectedGroup={selectedGroup}
                laptop={laptop}
              />
            </Suspense>
          ) : (
            !error && <Loading label="Loading workspace" />
          )}
        </main>
      </div>
      <video
        ref={laptop.videoRef}
        className="camera-capture-source"
        autoPlay
        playsInline
        muted
        aria-hidden="true"
      />
      <Modal open={mobile} onClose={() => setMobile(false)} title="Navigation">
        <div className="mobile-navigation">{navContent}</div>
      </Modal>
      <EnrollmentDialog
        open={enroll || !!refreshPerson}
        refreshPerson={refreshPerson}
        onClose={() => {
          setEnroll(false);
          setRefreshPerson(null);
        }}
        onSaved={() => {
          setEnroll(false);
          setRefreshPerson(null);
          void refresh();
          navigate("people");
        }}
      />
      <PersonDialog
        person={person}
        onClose={() => setPerson(null)}
        onSaved={() => void refresh()}
        accuracyModel={data?.engine.accuracy_model}
        onRefreshFaces={(target) => {
          setPerson(null);
          setRefreshPerson(target);
        }}
      />
      <ManualDialog
        open={manual}
        target={manualTarget}
        onClose={() => setManual(false)}
        people={data?.people ?? []}
        onSaved={() => void refresh()}
      />
      <Modal
        open={help}
        onClose={() => setHelp(false)}
        title="Get started with AttendAI"
      >
        <ol className="workspace-help-list">
          <li>
            <strong>Connect a camera.</strong> Share your laptop camera in Live
            Monitor, or connect a separate camera.
          </li>
          <li>
            <strong>Add a person.</strong> Enroll someone from People or review
            camera samples in Name People.
          </li>
          <li>
            <strong>Check attendance.</strong> Verified sightings from an
            attendance camera create check-ins. You can add a manual entry in
            Attendance.
          </li>
        </ol>
        <div className="modal-actions">
          <Button onClick={() => setHelp(false)}>Got it</Button>
        </div>
      </Modal>
      <Modal
        open={discardPage !== null}
        onClose={() => setDiscardPage(null)}
        title="Discard unsaved changes?"
        description="Your changes have not been saved."
      >
        <div className="modal-actions">
          <Button
            variant="secondary"
            autoFocus
            onClick={() => setDiscardPage(null)}
          >
            Keep editing
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              dirty.current = false;
              const next = discardPage;
              setDiscardPage(null);
              if (next) navigate(next);
            }}
          >
            Discard changes
          </Button>
        </div>
      </Modal>
      <Modal
        open={logout}
        onClose={() => {
          if (!signingOut) setLogout(false);
        }}
        title="Sign out?"
        description="Browser camera sharing ends when you sign out. Cameras connected directly to the server keep running."
      >
        <div className="modal-actions">
          <Button
            variant="secondary"
            onClick={() => setLogout(false)}
            disabled={signingOut}
          >
            Cancel
          </Button>
          <Button
            busy={signingOut}
            onClick={async () => {
              setSigningOut(true);
              try {
                await api("/logout", json("POST"));
                stopLaptop();
                setData(null);
                setLogout(false);
                setAccess("login");
                setPerson(null);
                setEnroll(false);
                setRefreshPerson(null);
                setManual(false);
              } catch (e) {
                toast(errorMessage(e), "error");
              } finally {
                setSigningOut(false);
              }
            }}
          >
            Sign out
          </Button>
        </div>
      </Modal>
    </div>
  );
}
