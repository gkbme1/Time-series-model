import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

// 提示条
const ToastCtx = createContext<(m: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const [show, setShow] = useState(false);
  const t = useRef<number>();
  const toast = useCallback((m: string) => {
    setMsg(m);
    setShow(true);
    clearTimeout(t.current);
    t.current = window.setTimeout(() => setShow(false), 2200);
  }, []);
  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className={"toast" + (show ? " show" : "")} role="status">{msg}</div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// 弹窗
export function Modal({ open, onClose, wide, children }: { open: boolean; onClose: () => void; wide?: boolean; children: ReactNode }) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) document.addEventListener("keydown", f);
    return () => document.removeEventListener("keydown", f);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal show" role="dialog" aria-modal="true" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={"box" + (wide ? " wide" : "")}>{children}</div>
    </div>
  );
}

export function Bar({ pct, warn }: { pct: number; warn?: boolean }) {
  return (
    <div className="row" style={{ flexWrap: "nowrap" }}>
      <div className="bar" style={{ flex: 1 }}><b style={{ width: `${Math.max(0, Math.min(100, pct))}%`, background: warn ? "var(--warn)" : "var(--teal)" }} /></div>
      <span className="note" style={{ width: 44, textAlign: "right" }}>{pct.toFixed(1)}%</span>
    </div>
  );
}

export const fmtN = (n: number) => n.toLocaleString("zh-CN");

export function Spinner({ text = "计算中…" }: { text?: string }) {
  return <p className="note" style={{ padding: "18px 0", textAlign: "center" }}>{text}</p>;
}

// 取 CSS 变量的实际颜色（变量里用了 light-dark()，图表库不认识，需让浏览器先算出 rgb）
export function cssVar(name: string, fallback = "#888") {
  const el = document.createElement("span");
  el.style.color = `var(${name}, ${fallback})`;
  el.style.display = "none";
  document.body.appendChild(el);
  const c = getComputedStyle(el).color;
  el.remove();
  return c || fallback;
}
