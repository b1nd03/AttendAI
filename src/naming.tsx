import { ScanFace, Trash2, UserRoundPlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage, json, preciseTime } from "./api";
import {
  Button,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  ModalCancel,
  PageHeading,
  Pagination,
  RouteSidebar,
  useToast,
} from "./components";
import type { Cluster } from "./types";
import type { ViewProps } from "./views";

const emptyForm = {
  name: "",
  code: "",
  department: "",
  person: "",
  consent: false,
  verified: false,
  attendance: true,
};

export function FaceInbox({
  data,
  refresh,
  onEnroll,
  selectedGroup,
}: ViewProps) {
  const [items, setItems] = useState<Cluster[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState(selectedGroup);
  const groupToOpen = useRef(selectedGroup);
  const [form, setForm] = useState(emptyForm);
  const [merge, setMerge] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [combineOpen, setCombineOpen] = useState(false);
  const [combineId, setCombineId] = useState("");
  const [combineVerified, setCombineVerified] = useState(false);
  const [combineError, setCombineError] = useState("");
  const [pendingSelection, setPendingSelection] = useState<{
    id?: string;
    page?: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [reload, setReload] = useState(0);
  const toast = useToast();
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const combineSource = items.find((item) => item.id === combineId);
  const dirty = JSON.stringify(form) !== JSON.stringify(emptyForm) || merge;
  const zone = data.settings.timezone;

  useEffect(() => {
    if (dirty || busy || deleteOpen || combineOpen) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const params = new URLSearchParams({ page: String(page) });
        if (groupToOpen.current) params.set("group_id", groupToOpen.current);
        const response = await api<{
          items: Cluster[];
          total: number;
          page: number;
        }>(`/review?${params}`, { signal: controller.signal });
        if (!controller.signal.aborted) {
          groupToOpen.current = undefined;
          setItems(response.items);
          setSelectedId((current) =>
            response.items.some((item) => item.id === current)
              ? current
              : response.items[0]?.id,
          );
          setTotal(response.total);
          setLoading(false);
          setError("");
          if (response.page !== page) setPage(response.page);
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setLoading(false);
          setError(errorMessage(e));
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [page, reload, dirty, busy, deleteOpen, combineOpen]);

  useEffect(() => {
    setForm(emptyForm);
    setMerge(false);
    setFormError("");
  }, [selected?.id]);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("presence-dirty", { detail: dirty }));
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => {
      window.removeEventListener("beforeunload", guard);
      window.dispatchEvent(
        new CustomEvent("presence-dirty", { detail: false }),
      );
    };
  }, [dirty]);

  function select(next: { id?: string; page?: number }) {
    if (dirty) {
      setPendingSelection(next);
      return;
    }
    applySelection(next);
  }
  function applySelection(next: { id?: string; page?: number }) {
    setForm(emptyForm);
    setMerge(false);
    setFormError("");
    setPendingSelection(null);
    if (next.page) {
      setLoading(true);
      setPage(next.page);
    }
    setSelectedId(next.id);
  }
  function finish(message: string) {
    setForm(emptyForm);
    setMerge(false);
    setDeleteOpen(false);
    setItems((previous) => previous.filter((item) => item.id !== selected?.id));
    setTotal((value) => Math.max(0, value - 1));
    setReload((value) => value + 1);
    refresh();
    toast(message);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (
      (merge && !form.person) ||
      (!merge &&
        (!form.name.trim() ||
          !form.code.trim() ||
          !form.department.trim() ||
          !form.consent)) ||
      !form.verified
    ) {
      setFormError(
        "Complete the identity fields and verify that every sample shows this person.",
      );
      return;
    }
    setBusy(true);
    setFormError("");
    try {
      await api(
        `/review/${selected.id}/${merge ? "assign" : "name"}`,
        json(
          "POST",
          merge
            ? { person_id: form.person, verified: form.verified }
            : {
                name: form.name,
                code: form.code,
                department: form.department,
                consent: form.consent,
                verified: form.verified,
                attendance_enabled: form.attendance,
              },
        ),
      );
      finish(
        merge
          ? "Face group merged."
          : `${form.name} is now known to your cameras.`,
      );
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function combine(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !combineSource || !combineVerified || busy) return;
    setBusy(true);
    setCombineError("");
    try {
      const updated = await api<Cluster>(
        `/review/${selected.id}/combine`,
        json("POST", { source_id: combineSource.id, verified: true }),
      );
      setItems((current) =>
        current
          .filter((item) => item.id !== combineSource.id)
          .map((item) => (item.id === updated.id ? updated : item)),
      );
      setTotal((current) => Math.max(0, current - 1));
      setForm((current) => ({ ...current, verified: false }));
      setCombineOpen(false);
      setReload((current) => current + 1);
      refresh();
      toast(
        "Face groups combined. Review the saved samples and give this person one name.",
      );
    } catch (e) {
      setCombineError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const sampleCount = selected?.sample_count ?? selected?.samples?.length ?? 0;
  const images = selected?.samples?.length
    ? selected.samples
    : selected?.sample
      ? [selected.sample]
      : [];
  const observations = selected?.observations ?? [];
  const first = observations[0];
  const selectedLabel = selected ? `#${selected.id.slice(0, 6)}` : "";

  return (
    <>
      <RouteSidebar mobile>
        <section className="attend-name-queue" aria-label="Unnamed face groups">
          <h2 className="attend-sidebar-label">Unnamed · {total}</h2>
          <div className="attend-name-options">
            {items.map((item) => (
              <button
                type="button"
                aria-pressed={item.id === selected?.id}
                key={item.id}
                disabled={busy}
                onClick={() => {
                  if (item.id !== selected?.id) select({ id: item.id });
                }}
              >
                <strong>#{item.id.slice(0, 6)}</strong>
                <span>
                  {item.camera_name || "Camera"} ·{" "}
                  {preciseTime(item.last_seen, zone)}
                </span>
              </button>
            ))}
          </div>
          {total > 12 && (
            <Pagination
              page={page}
              total={total}
              size={12}
              onChange={(value) => select({ page: value })}
            />
          )}
        </section>
      </RouteSidebar>
      <section className="attend-name-page">
        {error && (
          <ErrorBox message={error} retry={() => setReload((v) => v + 1)} />
        )}
        {loading ? (
          <Loading label="Loading face groups" />
        ) : selected ? (
          <>
            <PageHeading
              section="Face review"
              title={`Name person ${selectedLabel}`}
              description={
                <>
                  First seen at{" "}
                  {first?.camera_name || selected.camera_name || "a camera"},{" "}
                  {preciseTime(selected.first_seen, zone)} · {sampleCount} face
                  samples ·{" "}
                  {first?.liveness != null &&
                  first.liveness >= data.settings.liveness_threshold
                    ? "Live-person check passed"
                    : "Live-person check unconfirmed"}
                </>
              }
            />
            <div className="attend-name-columns">
              <div className="attend-name-evidence">
                <h2>Review the captured faces</h2>
                <div className="attend-sample-grid">
                  {Array.from({ length: images.length }, (_, index) => (
                    <div className="attend-sample" key={index}>
                      <img
                        src={`data:image/jpeg;base64,${images[index]}`}
                        alt={`Identity verification sample ${index + 1}`}
                      />
                      <span className="mono">Sample {index + 1}</span>
                    </div>
                  ))}
                </div>
                {selected.qualities?.length ? (
                  <details className="camera-advanced face-quality-details">
                    <summary>Face sample details</summary>
                    <ul>
                      {selected.qualities.map((quality, index) => (
                        <li key={index}>
                          Sample {index + 1} · quality{" "}
                          {Math.round(quality * 100)}%
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <section className="attend-observations panel">
                  <h2>Seen at</h2>
                  {observations.length ? (
                    <ul>
                      {observations.map((observation, index) => (
                        <li key={`${observation.created_at}-${index}`}>
                          <span>{observation.camera_name}</span>
                          <time
                            className="mono"
                            dateTime={observation.created_at}
                          >
                            {preciseTime(observation.created_at, zone)}
                          </time>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="attend-muted">
                      {selected.camera_name || "Camera"} ·{" "}
                      {preciseTime(selected.last_seen, zone)}
                    </p>
                  )}
                </section>
              </div>
              <form
                className="attend-name-form panel"
                noValidate
                onSubmit={save}
              >
                {merge ? (
                  <Field label="Existing person" id="existing-person">
                    <select
                      id="existing-person"
                      value={form.person}
                      onChange={(e) =>
                        setForm({ ...form, person: e.target.value })
                      }
                    >
                      <option value="">Choose a person</option>
                      {data.people.map((person) => (
                        <option key={person.id} value={person.id}>
                          {person.name} · {person.code}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : (
                  <>
                    <Field id="face-name" label="Full name">
                      <input
                        id="face-name"
                        value={form.name}
                        maxLength={100}
                        placeholder="e.g. Dilan Rashid"
                        onChange={(e) =>
                          setForm({ ...form, name: e.target.value })
                        }
                      />
                    </Field>
                    <Field id="face-code" label="ID number">
                      <input
                        id="face-code"
                        value={form.code}
                        maxLength={40}
                        placeholder="e.g. 2026-0147"
                        onChange={(e) =>
                          setForm({ ...form, code: e.target.value })
                        }
                      />
                    </Field>
                    <Field id="face-department" label="Group / class">
                      <input
                        id="face-department"
                        value={form.department}
                        maxLength={80}
                        placeholder="e.g. Computer Science, Year 3"
                        onChange={(e) =>
                          setForm({ ...form, department: e.target.value })
                        }
                      />
                    </Field>
                    <label className="check-field">
                      <input
                        type="checkbox"
                        checked={form.attendance}
                        onChange={(e) =>
                          setForm({ ...form, attendance: e.target.checked })
                        }
                      />
                      <span>Enable attendance for this person</span>
                    </label>
                    <label className="check-field">
                      <input
                        type="checkbox"
                        checked={form.consent}
                        onChange={(e) =>
                          setForm({ ...form, consent: e.target.checked })
                        }
                      />
                      <span>
                        This person explicitly consented to biometric
                        attendance.
                      </span>
                    </label>
                  </>
                )}
                <label className="check-field">
                  <input
                    type="checkbox"
                    checked={form.verified}
                    onChange={(e) =>
                      setForm({ ...form, verified: e.target.checked })
                    }
                  />
                  <span>
                    I reviewed every sample and verified this person's identity
                    in person.
                  </span>
                </label>
                {formError && !deleteOpen && <ErrorBox message={formError} />}
                <Button type="submit" busy={busy}>
                  {merge
                    ? "Save verified match"
                    : form.attendance
                      ? "Save and enable attendance"
                      : "Save person"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    setMerge((v) => !v);
                    setFormError("");
                  }}
                >
                  {merge ? "Name a new person" : "Merge with existing person"}
                </Button>
                <details className="camera-advanced review-more">
                  <summary>More review actions</summary>
                  <div className="review-more-actions">
                    {items.length > 1 && (
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => {
                          setCombineId("");
                          setCombineVerified(false);
                          setCombineError("");
                          setCombineOpen(true);
                        }}
                      >
                        Combine unnamed groups
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="secondary"
                      className="attend-delete-outline"
                      disabled={busy}
                      onClick={() => {
                        setFormError("");
                        setDeleteOpen(true);
                      }}
                    >
                      <Trash2 size={15} />
                      Delete face group
                    </Button>
                  </div>
                </details>
              </form>
            </div>
          </>
        ) : (
          <>
            <PageHeading
              section="Face review"
              title="Name People"
              description="Review faces saved by your cameras and give each person a name."
            />
            <section className="panel">
              <Empty
                title="No unnamed faces"
                description={
                  data.settings.retain_unknowns
                    ? "New face groups will appear after a connected camera captures clear samples."
                    : "Enable face collection from Settings or when connecting a camera."
                }
                icon={<ScanFace size={24} />}
                action={
                  <Button variant="secondary" onClick={onEnroll}>
                    <UserRoundPlus size={16} />
                    Enroll manually
                  </Button>
                }
              />
            </section>
          </>
        )}
      </section>
      <Modal
        open={combineOpen}
        wide
        onClose={() => {
          if (!busy) setCombineOpen(false);
        }}
        title="Combine unnamed groups"
        description="Compare both groups before combining. The app keeps up to five samples across the two views, removes the extra crops and combines their sighting history."
      >
        <form noValidate onSubmit={combine}>
          <Field id="combine-face-group" label="Other group on this page">
            <select
              id="combine-face-group"
              value={combineId}
              disabled={busy}
              onChange={(event) => {
                setCombineId(event.target.value);
                setCombineVerified(false);
                setCombineError("");
              }}
            >
              <option value="">Choose a face group</option>
              {items
                .filter((item) => item.id !== selected?.id)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    #{item.id.slice(0, 6)} · {item.camera_name || "Camera"} ·{" "}
                    {preciseTime(item.last_seen, zone)}
                  </option>
                ))}
            </select>
          </Field>
          <div className="combine-face-evidence">
            {[selected, combineSource].map((group, index) => (
              <section
                key={index}
                aria-label={
                  index === 0 ? "Current face group" : "Other face group"
                }
              >
                <h3>{group ? `#${group.id.slice(0, 6)}` : "Other group"}</h3>
                {group ? (
                  <div className="attend-sample-grid">
                    {(group.samples?.length
                      ? group.samples
                      : [group.sample]
                    ).map((sample, sampleIndex) => (
                      <div className="attend-sample" key={sampleIndex}>
                        <img
                          src={`data:image/jpeg;base64,${sample}`}
                          alt={`Group ${group.id.slice(0, 6)} sample ${sampleIndex + 1}`}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="attend-muted">
                    Choose another group to compare.
                  </p>
                )}
              </section>
            ))}
          </div>
          <label className="check-field">
            <input
              type="checkbox"
              checked={combineVerified}
              disabled={!combineSource || busy}
              onChange={(event) => setCombineVerified(event.target.checked)}
            />
            <span>
              I reviewed both groups and verified they show the same person.
            </span>
          </label>
          {combineError && <ErrorBox message={combineError} />}
          <div className="modal-actions">
            <ModalCancel disabled={busy} />
            <Button
              type="submit"
              busy={busy}
              disabled={!combineSource || !combineVerified}
            >
              Combine groups
            </Button>
          </div>
        </form>
      </Modal>
      <Modal
        open={deleteOpen}
        onClose={() => {
          if (!busy) setDeleteOpen(false);
        }}
        title="Delete this face group?"
        description="The saved face images and templates will be removed. Anonymous sighting times remain."
      >
        {formError && <ErrorBox message={formError} />}
        <div className="modal-actions">
          <Button
            variant="secondary"
            autoFocus
            onClick={() => setDeleteOpen(false)}
            disabled={busy}
          >
            Keep group
          </Button>
          <Button
            variant="danger"
            busy={busy}
            onClick={async () => {
              if (!selected) return;
              setBusy(true);
              setFormError("");
              try {
                await api(`/review/${selected.id}`, json("DELETE"));
                finish("Face group deleted.");
              } catch (e) {
                setFormError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Delete face group
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!pendingSelection}
        onClose={() => setPendingSelection(null)}
        title="Discard unsaved changes?"
        description="Choose whether to keep editing this identity or switch to another group."
      >
        <div className="modal-actions">
          <Button
            variant="secondary"
            autoFocus
            onClick={() => setPendingSelection(null)}
          >
            Keep editing
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (pendingSelection) applySelection(pendingSelection);
            }}
          >
            Discard changes
          </Button>
        </div>
      </Modal>
    </>
  );
}
