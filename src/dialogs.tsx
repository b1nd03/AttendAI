import {
  Camera,
  Check,
  Eye,
  EyeOff,
  FileImage,
  LockKeyhole,
  Plus,
  RotateCcw,
  ScanFace,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, json } from "./api";
import {
  Avatar,
  Badge,
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  ModalCancel,
  useToast,
} from "./components";
import { useCamera } from "./camera";
import type { ManualAttendanceTarget, Person, Template } from "./types";

export function WorkspaceAccess({
  configured,
  onSuccess,
  onConflict,
}: {
  configured: boolean;
  onSuccess: () => void;
  onConflict: () => void;
}) {
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  return (
    <main className="workspace-access">
      <section className="workspace-access-card" aria-labelledby="access-title">
        <div className="workspace-access-brand">
          <ScanFace size={25} />
          <span>
            Attend<span className="brand-dot">AI</span>
          </span>
        </div>
        <h1 id="access-title">
          {configured ? "Sign in to AttendAI" : "Set up AttendAI"}
        </h1>
        <p>
          {configured
            ? "Enter your workspace password to manage cameras and attendance."
            : "Create a password to open your workspace. Camera feeds and attendance stay on this computer."}
        </p>
        <form
          noValidate
          onSubmit={async (e) => {
            e.preventDefault();
            if (password.length < 12) {
              setError("Use a password with at least 12 characters.");
              input.current?.focus();
              return;
            }
            setBusy(true);
            setError("");
            try {
              await api(
                configured ? "/login" : "/setup",
                json("POST", { password }),
              );
              setPassword("");
              onSuccess();
            } catch (e) {
              if (e instanceof ApiError && e.status === 409) onConflict();
              setError(errorMessage(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field
            label={configured ? "Password" : "Create an administrator password"}
            id="password"
            hint="At least 12 characters. Keep this password somewhere safe."
          >
            <div className="password-wrap">
              <input
                ref={input}
                id="password"
                type={show ? "text" : "password"}
                autoComplete={configured ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={!!error}
                aria-describedby={error ? "auth-error" : "password-help"}
              />
              <button
                type="button"
                className="icon-button"
                aria-label={show ? "Hide password" : "Show password"}
                aria-pressed={show}
                onClick={() => setShow(!show)}
              >
                {show ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </Field>
          {error && (
            <div id="auth-error">
              <ErrorBox message={error} />
            </div>
          )}
          <div className="modal-actions">
            <Button busy={busy} type="submit">
              {configured ? "Sign in" : "Create workspace"}
            </Button>
          </div>
        </form>
      </section>
    </main>
  );
}

export function EnrollmentDialog({
  open,
  onClose,
  onSaved,
  refreshPerson,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  refreshPerson?: Person | null;
}) {
  const camera = useCamera();
  const toast = useToast();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [department, setDepartment] = useState("");
  const [consent, setConsent] = useState(false);
  const [verified, setVerified] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const cancelCapture = useRef(false);
  useEffect(() => {
    if (open) {
      setName("");
      setCode("");
      setDepartment("");
      setConsent(false);
      setVerified(false);
      setFiles([]);
      setError("");
      setFieldErrors({});
      setSaved(false);
    } else {
      cancelCapture.current = true;
      camera.stop();
    }
  }, [open, camera.stop]);
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach(URL.revokeObjectURL);
  }, [files]);
  const dirty =
    !saved &&
    Boolean(name || code || department || files.length || consent || verified);
  useEffect(() => {
    if (!open || !dirty) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [open, dirty]);
  const close = () => {
    if (!busy && !capturing) {
      cancelCapture.current = true;
      camera.stop();
      onClose();
    }
  };
  const capture = async () => {
    cancelCapture.current = false;
    setCapturing(true);
    setError("");
    const shots: File[] = [];
    try {
      for (let i = 0; i < 5; i++) {
        if (cancelCapture.current) break;
        shots.push(
          new File([await camera.capture()], `capture-${i}.jpg`, {
            type: "image/jpeg",
          }),
        );
        setFiles([...shots]);
        await new Promise((r) => setTimeout(r, 650));
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setCapturing(false);
    }
  };
  const choose = (chosen: FileList | null) => {
    if (!chosen) return;
    const items = Array.from(chosen);
    if (
      items.length < 3 ||
      items.length > 5 ||
      items.some(
        (f) =>
          !["image/jpeg", "image/png"].includes(f.type) || f.size > 5_000_000,
      )
    ) {
      setError("Choose 3–5 JPEG or PNG images, at most 5 MB each.");
      return;
    }
    setFiles(items);
    setError("");
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors: Record<string, string> = {};
    if (!refreshPerson && name.trim().length < 2)
      errors["person-name"] = "Enter a name with at least 2 characters.";
    if (!refreshPerson && !code.trim())
      errors["person-code"] = "Enter an employee or student ID.";
    if (!refreshPerson && !department.trim())
      errors["person-department"] = "Enter a department or group.";
    if (!consent) errors["consent"] = "Explicit biometric consent is required.";
    if (!verified)
      errors["verified"] = "Confirm that you verified this person in person.";
    setFieldErrors(errors);
    if (Object.keys(errors).length) {
      document.getElementById(Object.keys(errors)[0])?.focus();
      return;
    }
    if (files.length < 3) {
      setError("Capture or choose at least 3 images before enrolling.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data = new FormData();
      Object.entries({
        ...(refreshPerson ? {} : { name, code, department }),
        consent: String(consent),
        supervised: String(verified),
      }).forEach(([k, v]) => data.append(k, v));
      files.forEach((f) => data.append("files", f));
      await api(
        refreshPerson ? `/people/${refreshPerson.id}/refresh-faces` : "/people",
        {
          method: "POST",
          body: data,
        },
      );
      setSaved(true);
      camera.stop();
      toast(
        refreshPerson
          ? "Face templates refreshed."
          : "Person enrolled. Face templates are ready.",
      );
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={close}
      title={
        refreshPerson
          ? `Update face photos for ${refreshPerson.name}`
          : "Enroll a person"
      }
      description={
        refreshPerson
          ? "Capture or choose 3–5 fresh photos of the enrolled person."
          : "A few clear images today make everyday check-ins easier."
      }
      wide
      dirty={dirty}
    >
      <form noValidate onSubmit={submit}>
        <div className="enroll-layout">
          <div>
            <div className="enroll-camera">
              <video
                ref={camera.videoRef}
                autoPlay
                muted
                playsInline
                className={camera.active ? "" : "invisible"}
              />
              {!camera.active && (
                <div className="camera-idle">
                  <ScanFace size={48} />
                  <strong>Put a face to the name</strong>
                  <span>Use even lighting and a plain background.</span>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void camera.start()}
                    busy={camera.starting}
                  >
                    <Camera size={16} />
                    Open camera
                  </Button>
                </div>
              )}
              {camera.active && (
                <div className="capture-controls">
                  <Button
                    type="button"
                    busy={capturing}
                    disabled={busy}
                    onClick={() => void capture()}
                  >
                    <Camera size={16} />
                    Capture 5 images
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={capturing}
                    onClick={camera.stop}
                  >
                    Stop camera
                  </Button>
                </div>
              )}
            </div>
            <p className="caption">
              Look straight ahead, then turn slightly between captures.
            </p>
            <label className="upload-button">
              <Upload size={16} />
              Or choose 3–5 images
              <input
                type="file"
                accept="image/jpeg,image/png"
                multiple
                onChange={(e) => choose(e.target.files)}
                disabled={busy || capturing}
              />
            </label>
            <p className="caption">
              JPEG or PNG · up to 5 MB each · no group photos
            </p>
            <div className="capture-previews">
              {previews.map((url, i) => (
                <div key={url}>
                  <img src={url} alt={`Enrollment image ${i + 1}`} />
                  <span>
                    <Check size={12} />
                    {i + 1}
                  </span>
                </div>
              ))}
              {Array.from({ length: 5 - previews.length }, (_, i) => (
                <div className="preview-empty" key={`empty-${i}`}>
                  <FileImage size={21} />
                </div>
              ))}
            </div>
          </div>
          <div className="enroll-fields">
            {!refreshPerson &&
              [
                ["person-name", "Full name", name, setName, "e.g. Dilan Ahmed"],
                [
                  "person-code",
                  "Employee or student ID",
                  code,
                  setCode,
                  "e.g. PS-013",
                ],
                [
                  "person-department",
                  "Department or group",
                  department,
                  setDepartment,
                  "e.g. Engineering",
                ],
              ].map(([id, label, value, setter, placeholder]) => (
                <Field
                  key={id as string}
                  id={id as string}
                  label={label as string}
                  error={fieldErrors[id as string]}
                >
                  <input
                    id={id as string}
                    value={value as string}
                    onChange={(e) =>
                      (setter as (v: string) => void)(e.target.value)
                    }
                    placeholder={placeholder as string}
                    maxLength={id === "person-code" ? 40 : 80}
                    aria-invalid={!!fieldErrors[id as string]}
                    aria-describedby={
                      fieldErrors[id as string] ? `${id}-help` : undefined
                    }
                  />
                </Field>
              ))}
            <div className="privacy-note">
              <ShieldCheck size={19} />
              <p>
                Only encrypted face templates are saved. Enrollment images are
                discarded after processing.
              </p>
            </div>
            <label className="check-field">
              <input
                id="consent"
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                aria-invalid={!!fieldErrors.consent}
                aria-describedby={
                  fieldErrors.consent ? "consent-error" : undefined
                }
              />
              <span>
                This person has explicitly consented to biometric attendance.
              </span>
            </label>
            {fieldErrors.consent && (
              <span id="consent-error" className="field-error">
                {fieldErrors.consent}
              </span>
            )}
            <label className="check-field">
              <input
                id="verified"
                type="checkbox"
                checked={verified}
                onChange={(e) => setVerified(e.target.checked)}
                aria-invalid={!!fieldErrors.verified}
                aria-describedby={
                  fieldErrors.verified ? "verified-error" : undefined
                }
              />
              <span>I have verified this person's identity in person.</span>
            </label>
            {fieldErrors.verified && (
              <span id="verified-error" className="field-error">
                {fieldErrors.verified}
              </span>
            )}
          </div>
        </div>
        {(error || camera.error) && (
          <ErrorBox message={error || camera.error} />
        )}
        {busy && <p role="status">Preparing encrypted face templates…</p>}
        <div className="modal-actions">
          <span className="caption">
            <LockKeyhole size={13} />
            Saved on this computer
          </span>
          <ModalCancel disabled={busy || capturing} />
          <Button type="submit" busy={busy} disabled={capturing}>
            <Plus size={17} />
            {refreshPerson ? "Update face photos" : "Enroll person"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function PersonDialog({
  person,
  onClose,
  onSaved,
  accuracyModel,
  onRefreshFaces,
}: {
  person: Person | null;
  onClose: () => void;
  onSaved: () => void;
  accuracyModel?: string | null;
  onRefreshFaces: (person: Person) => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [attendanceEnabled, setAttendanceEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rollbackId, setRollbackId] = useState<string | null>(null);
  const toast = useToast();
  const load = () => {
    if (!person) return;
    setLoading(true);
    setError("");
    api<Template[]>(`/templates/${person.id}`)
      .then(setTemplates)
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    setConfirmDelete(false);
    setRollbackId(null);
    setError("");
    if (!person) return;
    setAttendanceEnabled(person.attendance_enabled !== 0);
    let cancelled = false;
    setLoading(true);
    api<Template[]>(`/templates/${person.id}`)
      .then((data) => {
        if (!cancelled) setTemplates(data);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [person]);
  return (
    <Modal
      open={!!person}
      onClose={() => {
        if (!busy) onClose();
      }}
      title={
        confirmDelete
          ? "Forget this person?"
          : rollbackId
            ? "Roll back this template?"
            : "Person profile"
      }
      description={
        confirmDelete
          ? "This permanently removes their face templates, evidence, and attendance from this workspace."
          : rollbackId
            ? "This template will stop contributing to recognition. Its replaced template is restored when available."
            : undefined
      }
    >
      {person && (
        <>
          <div className="profile-heading">
            <Avatar name={person.name} size="large" />
            <div>
              <h3>{person.name}</h3>
              <p>
                {person.code} · {person.department}
              </p>
            </div>
          </div>
          {error && <ErrorBox message={error} />}{" "}
          {confirmDelete ? (
            <>
              <p>
                This action cannot be undone. Audit records retain an anonymous
                internal identifier.
              </p>
              <div className="modal-actions">
                <Button
                  variant="secondary"
                  autoFocus
                  disabled={busy}
                  onClick={() => setConfirmDelete(false)}
                >
                  Keep person
                </Button>
                <Button
                  variant="danger"
                  busy={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api(`/people/${person.id}`, json("DELETE"));
                      toast(
                        "Person forgotten. Biometrics and attendance deleted.",
                      );
                      onSaved();
                      onClose();
                    } catch (e) {
                      setError(errorMessage(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <Trash2 size={15} />
                  Forget person
                </Button>
              </div>
            </>
          ) : rollbackId ? (
            <div className="modal-actions">
              <Button
                variant="secondary"
                autoFocus
                onClick={() => setRollbackId(null)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button
                busy={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api(
                      `/templates/${rollbackId}/rollback`,
                      json("POST"),
                    );
                    toast("Template rolled back.");
                    setRollbackId(null);
                    load();
                    onSaved();
                  } catch (e) {
                    setError(errorMessage(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <RotateCcw size={16} />
                Roll back template
              </Button>
            </div>
          ) : (
            <>
              <div className="profile-consent">
                <ShieldCheck size={17} />
                <span>Biometric consent recorded</span>
                <Badge tone="success">Enrolled</Badge>
              </div>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={attendanceEnabled}
                  disabled={busy}
                  onChange={async (event) => {
                    const enabled = event.target.checked;
                    setBusy(true);
                    setError("");
                    try {
                      await api(
                        `/people/${person.id}/attendance`,
                        json("PATCH", { enabled }),
                      );
                      setAttendanceEnabled(enabled);
                      onSaved();
                      toast(
                        enabled
                          ? "Automatic attendance enabled."
                          : "Automatic attendance paused.",
                      );
                    } catch (e) {
                      setError(errorMessage(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
                <span>Enable automatic attendance for this person</span>
              </label>
              <details className="camera-advanced">
                <summary>Saved face details</summary>
                <div className="section-label">
                  Face gallery{" "}
                  <span>
                    {templates.filter((t) => t.active).length} active templates
                  </span>
                </div>
                {loading ? (
                  <Loading label="Loading face gallery" />
                ) : (
                  <div className="template-list">
                    {templates.map((t, i) => (
                      <div key={t.id}>
                        <div className="template-icon">
                          <ScanFace size={19} />
                        </div>
                        <div>
                          <strong>
                            {t.source === "enrollment"
                              ? "Verified anchor"
                              : t.source === "auto"
                                ? "Learned template"
                                : "Reviewed template"}{" "}
                            {i + 1}
                          </strong>
                          <span>
                            {t.active ? "Active" : "Inactive"} · quality{" "}
                            {Math.round(t.quality * 100)}/100
                          </span>
                        </div>
                        {t.active && t.source !== "enrollment" ? (
                          <button
                            className="icon-button"
                            aria-label={`Roll back template ${i + 1}`}
                            onClick={() => setRollbackId(t.id)}
                          >
                            <RotateCcw size={16} />
                          </button>
                        ) : (
                          <LockKeyhole size={15} className="muted" />
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <p className="caption">
                  Verified anchors remain fixed. Adaptive templates can be
                  rolled back individually.
                </p>
              </details>
              {accuracyModel && (
                <div className="profile-consent">
                  <ScanFace size={17} />
                  <span>
                    {(person.accuracy_templates ?? 0) >= 3
                      ? `Extra face checks available`
                      : "Add new photos to improve matching for this person."}
                    <small className="caption">
                      The second model checks close matches once every enrolled
                      person has its templates.
                    </small>
                  </span>
                  <Button
                    variant="secondary"
                    onClick={() => onRefreshFaces(person)}
                  >
                    Update face photos
                  </Button>
                </div>
              )}
              <div className="modal-actions">
                <Button
                  variant="ghost"
                  className="danger-text"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 size={16} />
                  Forget person
                </Button>
                <Button variant="secondary" onClick={onClose}>
                  Close profile
                </Button>
              </div>
            </>
          )}
        </>
      )}
    </Modal>
  );
}

export function ManualDialog({
  open,
  target,
  onClose,
  people,
  onSaved,
}: {
  open: boolean;
  target?: ManualAttendanceTarget;
  onClose: () => void;
  people: Person[];
  onSaved: () => void;
}) {
  const [person, setPerson] = useState("");
  const [kind, setKind] = useState("in");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  useEffect(() => {
    if (open) {
      setPerson(target?.personId ?? "");
      setKind(target?.kind ?? "in");
      setNote("");
      setError("");
    }
  }, [open, target]);
  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Record attendance manually"
      description={
        target
          ? "Verify the person in front of the camera before recording attendance. Every entry is added to the audit log."
          : "Use this when a person cannot use face recognition. Every entry is added to the audit log."
      }
      dirty={
        !!note ||
        person !== (target?.personId ?? "") ||
        kind !== (target?.kind ?? "in")
      }
    >
      {people.length === 0 ? (
        <Empty
          title="Enroll someone first"
          description="Manual attendance is available for people in your directory."
        />
      ) : (
        <form
          noValidate
          onSubmit={async (e) => {
            e.preventDefault();
            if (!person || note.trim().length < 3) {
              setError(
                "Choose a person and enter a reason of at least 3 characters.",
              );
              document
                .getElementById(!person ? "manual-person" : "manual-note")
                ?.focus();
              return;
            }
            setBusy(true);
            setError("");
            try {
              const result = await api<{ message: string }>(
                "/attendance/manual",
                json("POST", { person_id: person, kind, note }),
              );
              toast(result.message);
              onSaved();
              onClose();
            } catch (e) {
              setError(errorMessage(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Person" id="manual-person">
            <select
              id="manual-person"
              value={person}
              onChange={(e) => setPerson(e.target.value)}
            >
              <option value="">Choose an enrolled person</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.code}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Attendance event" id="manual-kind">
            <select
              id="manual-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="in">Check-in</option>
              <option value="out">Check-out</option>
            </select>
          </Field>
          <Field label="Reason for manual entry" id="manual-note">
            <textarea
              id="manual-note"
              rows={3}
              value={note}
              maxLength={300}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Camera unavailable; identity checked in person"
            />
          </Field>
          {error && <ErrorBox message={error} />}
          <div className="modal-actions">
            <ModalCancel disabled={busy} />
            <Button type="submit" busy={busy}>
              Record attendance
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
