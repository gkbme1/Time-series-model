import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, Project, ScenarioLite, Target } from "./api";
import { FN_NAME, FNS, go, useHash } from "./router";
import { Modal, useToast } from "./ui";
import Home from "./pages/Home";
import ProjectPage from "./pages/Project";
import ScenarioOverview from "./pages/ScenarioOverview";
import Wizard from "./pages/Wizard";
import Templates from "./pages/Templates";
import DataPage from "./pages/Data";
import Placeholder from "./pages/Placeholder";
import RegimePage from "./pages/Regime";
import ErrorBoundary from "./components/ErrorBoundary";
import FeaturePage from "./pages/Feature";
import LabPage from "./pages/Lab";
import ValidatePage from "./pages/Validate";

const LS = "tsm.project";
export const tgLabel = (g?: Target) =>
  g ? `${g.name}（${g.type === "fc" ? "预测未来 " + g.horizons.join("/") + " 分钟" : "软测量"}）` : "";
export const statusClass = (st: string) =>
  ({ 在线运行: "t-ok", 已发布: "t-ok", 验证中: "t-warn", 特征工程: "t-teal" } as Record<string, string>)[st] || "t-mute";
const dotColor = (st: string) => ({ 在线运行: "#51cf66", 验证中: "#ffa94d", 特征工程: "#43b3bb" } as Record<string, string>)[st] || "#868e96";

export default function App() {
  const hash = useHash();
  const qc = useQueryClient();
  const toast = useToast();
  const [pid, setPid] = useState<number | null>(() => {
    const v = localStorage.getItem(LS);
    return v === null ? -1 : v === "" ? null : +v;
  });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/api/projects") });
  // 首次进入：默认打开最近的项目
  useEffect(() => {
    if (pid === -1 && projects.data) setPid(projects.data[0]?.id ?? null);
  }, [pid, projects.data]);
  useEffect(() => { if (pid !== -1) localStorage.setItem(LS, pid === null ? "" : String(pid)); }, [pid]);
  const proj = projects.data?.find((p) => p.id === pid) || null;
  const [dlg, setDlg] = useState<"" | "open" | "new">("");
  const [opened, setOpened] = useState<Set<string>>(new Set(["root"]));

  const route = useMemo(() => {
    const m = hash.match(/^s(\d+)-(\w+)$/);
    if (hash === "templates") return { cur: "templates" as const };
    if (!proj) return { cur: "home" as const };
    if (hash === "newscen") return { cur: "wizard" as const };
    if (m && FN_NAME[m[2]]) {
      const sc = proj.scenarios.find((s) => s.id === +m[1]);
      if (sc) return { cur: m[2], sc };
    }
    return { cur: "proj" as const };
  }, [hash, proj]);
  const sc = (route as { sc?: ScenarioLite }).sc;
  useEffect(() => { if (sc) setOpened((o) => new Set(o).add("root").add("s" + sc.id)); }, [sc?.id]);

  const openProject = async (id: number) => {
    await api.post(`/api/projects/${id}/open`);
    await qc.invalidateQueries({ queryKey: ["projects"] });
    setPid(id);
    const p = projects.data?.find((x) => x.id === id);
    setOpened(new Set(["root", ...(p?.scenarios[0] ? ["s" + p.scenarios[0].id] : [])]));
    setDlg("");
    go("proj");
    toast("已打开项目：" + (p?.name || ""));
  };

  let crumb = "", ttl = "";
  if (route.cur === "templates") { crumb = "系统"; ttl = "场景模板库"; }
  else if (route.cur === "home") ttl = "欢迎使用";
  else if (route.cur === "wizard") { crumb = proj!.name; ttl = "新建场景"; }
  else if (sc) { crumb = proj!.name + " / " + sc.name; ttl = FN_NAME[route.cur]; }
  else { crumb = "项目"; ttl = proj!.name; }

  let page: JSX.Element;
  if (route.cur === "templates") page = <Templates />;
  else if (route.cur === "home") page = <Home projects={projects.data || []} onOpen={openProject} onNew={() => setDlg("new")} onOpenDlg={() => setDlg("open")} />;
  else if (route.cur === "wizard") page = <Wizard project={proj!} />;
  else if (route.cur === "proj") page = <ProjectPage project={proj!} />;
  else if (route.cur === "sov") page = <ScenarioOverview sc={sc!} />;
  else if (route.cur === "data") page = <DataPage sid={sc!.id} key={sc!.id} />;
  else if (route.cur === "regime") page = <RegimePage sid={sc!.id} key={sc!.id} />;
  else if (route.cur === "feature") page = <FeaturePage sid={sc!.id} key={sc!.id} />;
  else if (route.cur === "lab") page = <LabPage sid={sc!.id} key={sc!.id} />;
  else if (route.cur === "validate") page = <ValidatePage sid={sc!.id} key={sc!.id} />;
  else page = <Placeholder name={FN_NAME[route.cur]} />;

  return (
    <div className="app">
      <header className="hdr">
        <div className="brand" style={{ padding: "0 12px 0 0" }}><i>T</i><span>时序建模与软测量平台<small>Demo · 示例数据</small></span></div>
        <div className="pname">{proj ? "项目：" + proj.name : "未打开项目"}</div>
        <button className="hb" onClick={() => setDlg("new")}>新建项目</button>
        <button className="hb" onClick={() => setDlg("open")}>打开项目</button>
        <button className="hb" disabled={!proj} onClick={() => { toast("已关闭项目：" + proj!.name); setPid(null); go("home"); }}>关闭项目</button>
        <span className="sp" />
        <button className="hb" onClick={() => go("templates")}>场景模板库</button>
      </header>
      <nav className="rail" aria-label="项目树">
        {!proj ? <p className="note" style={{ color: "#8b96a1", padding: 10 }}>未打开项目</p> : (
          <div className={opened.has("root") ? "open" : ""}>
            <button className={"tn root" + (route.cur === "proj" ? " on" : "")} onClick={() => { setOpened((o) => new Set(o).add("root")); go("proj"); }}>
              <span className="caret" onClick={(e) => { e.stopPropagation(); setOpened((o) => { const n = new Set(o); n.has("root") ? n.delete("root") : n.add("root"); return n; }); }}>▶</span>{proj.name}
            </button>
            <div className="tk">
              {proj.scenarios.map((s) => (
                <div key={s.id} className={opened.has("s" + s.id) ? "open" : ""}>
                  <button className={"tn" + (sc?.id === s.id && route.cur === "sov" ? " on" : "")} onClick={() => { setOpened((o) => new Set(o).add("s" + s.id)); go(`s${s.id}-sov`); }}>
                    <span className="caret" onClick={(e) => { e.stopPropagation(); setOpened((o) => { const n = new Set(o); const k = "s" + s.id; n.has(k) ? n.delete(k) : n.add(k); return n; }); }}>▶</span>
                    <span className="sd" style={{ background: dotColor(s.status) }} />{s.name}<span className="ty">{s.type}</span>
                  </button>
                  <div className="tk">
                    {FNS.slice(1).map(([k, n]) => (
                      <button key={k} className={"tn" + (sc?.id === s.id && route.cur === k ? " on" : "")} onClick={() => go(`s${s.id}-${k}`)}>{n}</button>
                    ))}
                  </div>
                </div>
              ))}
              <button className="tn add" onClick={() => go("newscen")}>＋ 新建场景</button>
            </div>
          </div>
        )}
      </nav>
      <main>
        <div className="top">
          <div><div className="crumb">{crumb}</div><h1>{ttl}</h1></div>
          {sc && (
            <div className="proj">
              <span className="tag t-acc tgt">目标：{tgLabel(sc.target)}</span>
              <span className="tag t-mute">{sc.type}</span>
              <span className="tag t-mute">{sc.template_name}</span>
              <span className="tag t-mute">{sc.furnace}</span>
              <span className={"tag " + statusClass(sc.status)}>{sc.status}</span>
            </div>
          )}
        </div>
        <ErrorBoundary resetKey={hash}>{page}</ErrorBoundary>
      </main>
      <OpenDialog open={dlg === "open"} onClose={() => setDlg("")} projects={projects.data || []} onOpen={openProject} />
      <NewProjectDialog open={dlg === "new"} onClose={() => setDlg("")} onCreated={(p) => { setPid(p.id); setOpened(new Set(["root"])); setDlg(""); go("proj"); toast("项目已创建，可以添加场景了"); }} />
    </div>
  );
}

function OpenDialog({ open, onClose, projects, onOpen }: { open: boolean; onClose: () => void; projects: Project[]; onOpen: (id: number) => void }) {
  const [q, setQ] = useState("");
  return (
    <Modal open={open} onClose={onClose}>
      <h2>打开项目<button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <input type="text" autoFocus placeholder="搜索项目名称" style={{ width: "100%", marginBottom: 10 }} value={q} onChange={(e) => setQ(e.target.value)} />
      <table><thead><tr><th>项目</th><th>装置</th><th className="r">场景</th><th></th></tr></thead>
        <tbody>{projects.filter((p) => !q.trim() || p.name.includes(q.trim())).map((p) => (
          <tr key={p.id}><td><b>{p.name}</b></td><td>{p.device}</td><td className="r">{p.scenarios.length}</td>
            <td className="r"><button className="pri" onClick={() => onOpen(p.id)}>打开</button></td></tr>))}</tbody></table>
    </Modal>
  );
}

function NewProjectDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (p: Project) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [dev, setDev] = useState("乙烯装置 · 裂解区");
  const [onto, setOnto] = useState("乙烯装置本体 v2.1");
  const [err, setErr] = useState("");
  const ok = async () => {
    if (!name.trim()) { setErr("请输入项目名称"); return; }
    try {
      const p = await api.post<Project>("/api/projects", { name, device: dev, ontology: onto });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      setName(""); setErr("");
      onCreated(p);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal open={open} onClose={onClose}>
      <h2>新建项目<button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <div className="form">
        <span className="lab">项目名称</span><input type="text" autoFocus placeholder="例如：乙烯装置裂解炉智能建模" value={name} onChange={(e) => { setName(e.target.value); setErr(""); }} />
        <span className="lab">所属装置</span><select value={dev} onChange={(e) => setDev(e.target.value)}><option>乙烯装置 · 裂解区</option><option>乙烯装置 · 分离区</option><option>煤气化装置 · 气化区</option></select>
        <span className="lab">数据源</span><select><option>本地文件（Demo，接口预留 XBase）</option></select>
        <span className="lab">本体模型</span><select value={onto} onChange={(e) => setOnto(e.target.value)}><option>乙烯装置本体 v2.1</option><option>煤气化装置本体 v1.0</option></select>
      </div>
      <p className="note" style={{ color: "var(--bad)" }}>{err}</p>
      <div className="row" style={{ justifyContent: "flex-end" }}><button onClick={onClose}>取消</button><button className="pri" onClick={ok}>创建</button></div>
    </Modal>
  );
}
