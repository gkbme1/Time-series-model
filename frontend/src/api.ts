// 后端接口封装。所有计算都在后端完成，前端只负责显示和编辑配置。
export class ApiError extends Error {}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `请求失败（${r.status}）`;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") msg = j.detail;
    } catch {}
    throw new ApiError(msg);
  }
  return r.json();
}

export const api = {
  get: <T,>(u: string) => req<T>("GET", u),
  post: <T,>(u: string, b?: unknown) => req<T>("POST", u, b ?? {}),
  put: <T,>(u: string, b: unknown) => req<T>("PUT", u, b),
  upload: <T,>(u: string, f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    return req<T>("POST", u, fd);
  },
};

export type Target = {
  name: string; type: "soft" | "fc"; horizons: number[]; unit: string; lo: number; hi: number;
  pick: "avg" | "t1"; output_tag: string;
};
export type ScenarioLite = {
  id: number; project_id: number; name: string; type: string; template_id: string; template_name: string;
  furnace: string; stage: string; status: string; model: string; target: Target; created_at: string;
};
export type Scenario = ScenarioLite & { config: any };
export type Project = {
  id: number; name: string; device: string; ontology: string; created_at: string; opened_at: string | null;
  scenarios: ScenarioLite[];
};
export type Tag = {
  tag: string; name: string; device: string; part: string; desc: string; unit: string; period: string;
  range_lo: number; range_hi: number; recommended: number; is_target: number; furnace: string;
};
export type Template = {
  id: string; name: string; desc: string; tags: string[]; device_type: string; calib_label: string; split: string;
  detail: [string, string][]; incomplete?: boolean; version?: string; regime_rules?: unknown[]; recommended?: string[];
};
export type Furnace = { unit: string; name: string; from: string; to: string; train_to: string };
