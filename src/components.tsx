import * as Dialog from "@radix-ui/react-dialog";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Inbox,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

export function Button({
  children,
  className = "",
  variant = "primary",
  busy = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy}
      className={`btn btn-${variant} ${className}`}
    >
      <span
        className={busy ? "button-content hidden-content" : "button-content"}
      >
        {children}
      </span>
      {busy && <LoaderCircle className="button-spinner spin" size={17} />}
    </button>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "blue";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function PageHeading({
  section,
  title,
  description,
  actions,
}: {
  section: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="page-heading-copy">
        <div className="eyebrow">{section}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
export function Avatar({
  name,
  size = "normal",
}: {
  name: string;
  size?: "normal" | "large";
}) {
  const n = [...name].reduce((s, c) => s + c.charCodeAt(0), 0) % 5;
  return (
    <span aria-hidden="true" className={`avatar avatar-${n} avatar-${size}`}>
      {name
        .split(" ")
        .map((x) => x[0])
        .slice(0, 2)
        .join("")}
    </span>
  );
}
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  wide = false,
  dirty = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
  dirty?: boolean;
}) {
  const [discard, setDiscard] = useState(false);
  const descriptionId = useId();
  const returnFocus = useRef<Element | null>(null);
  const wasOpen = useRef(false);
  if (open && !wasOpen.current) returnFocus.current = document.activeElement;
  wasOpen.current = open;
  useEffect(() => {
    if (!open) setDiscard(false);
  }, [open]);
  const close = () => {
    if (dirty) setDiscard(true);
    else onClose();
  };
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          aria-describedby={description && !discard ? descriptionId : undefined}
          className={`modal ${wide ? "modal-wide" : ""}`}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            const target = returnFocus.current;
            requestAnimationFrame(() => {
              if (target instanceof HTMLElement && target.isConnected)
                target.focus();
              else document.getElementById("main-content")?.focus();
            });
          }}
          onPointerDownOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => {
            if (dirty) {
              e.preventDefault();
              setDiscard(true);
            }
          }}
        >
          <div className="modal-heading">
            <div>
              <Dialog.Title>
                {discard ? "Discard unsaved changes?" : title}
              </Dialog.Title>
              {description && !discard && (
                <Dialog.Description id={descriptionId}>
                  {description}
                </Dialog.Description>
              )}
            </div>
            <button
              className="icon-button"
              aria-label="Close dialog"
              onClick={close}
            >
              <X size={20} />
            </button>
          </div>
          {discard ? (
            <>
              <p>Your unsaved changes will be discarded.</p>
              <div className="modal-actions">
                <Button
                  variant="secondary"
                  autoFocus
                  onClick={() => setDiscard(false)}
                >
                  Keep editing
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    setDiscard(false);
                    onClose();
                  }}
                >
                  Discard changes
                </Button>
              </div>
            </>
          ) : (
            children
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function ModalCancel({ disabled = false }: { disabled?: boolean }) {
  return (
    <Dialog.Close asChild>
      <Button type="button" variant="secondary" disabled={disabled}>
        Cancel
      </Button>
    </Dialog.Close>
  );
}
export function RouteSidebar({
  children,
  mobile = false,
}: {
  children: ReactNode;
  mobile?: boolean;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 901px)");
    const sync = () =>
      setHost(query.matches ? document.getElementById("route-sidebar") : null);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  return host ? (
    createPortal(children, host)
  ) : mobile ? (
    <div className="route-tools-mobile">{children}</div>
  ) : null;
}
export function Field({
  label,
  id,
  error,
  hint,
  children,
}: {
  label: string;
  id: string;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {(error || hint) && (
        <span
          id={`${id}-help`}
          className={error ? "field-error" : "field-hint"}
        >
          {error || hint}
        </span>
      )}
    </div>
  );
}
export function ErrorBox({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div role="alert" className="error-box">
      <AlertCircle size={18} />
      <span>{message}</span>
      {retry && (
        <Button variant="ghost" onClick={retry}>
          Retry
        </Button>
      )}
    </div>
  );
}
export function Empty({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon || <Inbox size={25} />}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Loading({ label = "Loading workspace" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={21} className="spin" />
      {label}
    </div>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = "Search people",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="search-field">
      <Search size={17} />
      <input
        ref={ref}
        aria-label={placeholder}
        type="search"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && (
        <button
          className="icon-button"
          aria-label="Clear search"
          onClick={() => {
            onChange("");
            ref.current?.focus();
          }}
        >
          <X size={15} />
        </button>
      )}
    </div>
  );
}
export function Pagination({
  page,
  total,
  size,
  onChange,
}: {
  page: number;
  total: number;
  size: number;
  onChange: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pagination">
      <span>
        {total === 0
          ? "0 results"
          : `${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total}`}
      </span>
      <nav aria-label="Pagination">
        <button
          aria-label="Previous page"
          disabled={page === 1}
          className="icon-button"
          onClick={() => onChange(page - 1)}
        >
          <ArrowLeft size={16} />
        </button>
        <span>
          Page {page} of {pages}
        </span>
        <button
          aria-label="Next page"
          disabled={page >= pages}
          className="icon-button"
          onClick={() => onChange(page + 1)}
        >
          <ArrowRight size={16} />
        </button>
      </nav>
    </div>
  );
}
const ToastContext = createContext<
  (text: string, tone?: "success" | "error") => void
>(() => {});
export const useToast = () => useContext(ToastContext);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ text: string; tone: string } | null>(
    null,
  );
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(id);
  }, [toast]);
  return (
    <ToastContext.Provider
      value={(text, tone = "success") => setToast({ text, tone })}
    >
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toast && (
          <div className={`toast toast-${toast.tone}`}>
            {toast.tone === "success" ? (
              <Check size={18} />
            ) : (
              <AlertCircle size={18} />
            )}
            <span>{toast.text}</span>
            <button
              className="icon-button"
              aria-label="Dismiss notification"
              onClick={() => setToast(null)}
            >
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    </ToastContext.Provider>
  );
}
