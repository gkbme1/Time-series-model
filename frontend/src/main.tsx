import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./ui";
import "./proto.css";
import "./extra.css";

// 窄屏下表格按卡片显示时，给每个单元格补上列名（沿用原型做法）
new MutationObserver(() => {
  for (const c of document.querySelectorAll<HTMLTableCellElement>(".stack td:not([data-l])")) {
    let i = 0, p: Element | null = c;
    while ((p = p.previousElementSibling)) i += (p as HTMLTableCellElement).colSpan;
    c.dataset.l = (c.closest("table") as HTMLTableElement).rows[0].cells[i]?.textContent || "";
  }
}).observe(document, { childList: true, subtree: true });

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 5000 } } });
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider><App /></ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
