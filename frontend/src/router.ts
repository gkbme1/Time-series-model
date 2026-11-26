import { useEffect, useState } from "react";

// 与原型一致的 hash 路由：#home #proj #templates #newscen #s1-data …
export function useHash(): string {
  const [h, setH] = useState(() => location.hash.slice(1));
  useEffect(() => {
    const f = () => setH(location.hash.slice(1));
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return h;
}
export const go = (h: string) => {
  if (location.hash.slice(1) === h) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = h;
  window.scrollTo(0, 0);
};

export const FNS: [string, string][] = [
  ["sov", "场景概览"], ["data", "选数据"], ["regime", "分工况"], ["feature", "加工输入"], ["lab", "训练模型"],
  ["validate", "检验效果"], ["deploy", "上线投用"], ["check", "在线校核"], ["iterate", "跟踪改进"],
];
export const FN_NAME: Record<string, string> = Object.fromEntries(FNS);
