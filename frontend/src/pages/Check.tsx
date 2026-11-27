import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { Spinner, useToast } from "../ui";
import { ClockBar } from "./Deploy";

type Row = { t: string; position: string; y: number | null; p: number | null; dev: number | null; source: string; status: string; note: string; id: number | null; counted: boolean; applicable: boolean | null; backfill: boolean };
type CP = {
  clock: any; instance: any; kpi: { n7: number; tc: number; ir: number; man: number; pending: number; latency: number | null }; offset: number; target: any;
  sources: string[][]; rows: Row[]; total: number; page: number; pages: number; pending: { id: number; t: string; position: string; value: number; source: string; who: string; instrument: string }[];
};
const ST: Record<string, string> = { 有效: "t-ok", 待审核: "t-acc", 质量异常: "t-warn", 已作废: "t-mute" };

export default function CheckPage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [src, setSrc] = useState("");
  const [st, setSt] = useState("");
  const [page, setPage] = useState(0);
  const q = useQuery({ queryKey: ["check", sid, src, st, page], queryFn: () => api.get<CP>(`/api/scenarios/${sid}/check?src=${encodeURIComponent(src)}&st=${encodeURIComponent(st)}&page=${page}`), refetchInterval: 3000, placeholderData: (p) => p });
  const C = q.data;
  const [f, setF] = useState({ t: "", pos: "出口段 1# 管", v: "", inst: "红外高温计 IR-03", who: "" });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  if (!C) return <Spinner text="加载中…" />;
  const refresh = () => qc.invalidateQueries({ queryKey: ["check", sid] });
  const review = async (id: number, action: string) => { await api.post(`/api/calib-records/${id}/review`, { action }); toast(action === "approve" ? "已审核通过，参与偏差计算" : "已作废，不参与计算"); refresh(); qc.invalidateQueries({ queryKey: ["metrics", sid] }); };
  const submit = async () => {
    setErr("");
    const v = parseFloat(f.v);
    if (!f.t) return setErr("请填写测量时刻");
    if (isNaN(v)) return setErr("请填写校核值");
    if (!f.who.trim()) return setErr("请填写测量人");
    try { await api.post(`/api/scenarios/${sid}/check/manual`, { measured_at: f.t.replace("T", " "), position: f.pos, value: v, instrument: f.inst, operator: f.who }); setF({ ...f, v: "" }); setSt("待审核"); setSrc(""); setPage(0); toast("已提交，等待审核"); refresh(); }
    catch (e) { setErr((e as Error).message); }
  };
  const onFile = async (file?: File) => {
    if (!file) return;
    try { const r = await api.upload<{ n: number; skipped: number; name: string }>(`/api/scenarios/${sid}/check/import`, file); setMsg(`已读取 ${r.name}，${r.n} 条进入待审核${r.skipped ? `；${r.skipped} 条因测量时刻晚于当前时刻或超出合理范围被跳过` : ""}`); setSt("待审核"); setPage(0); refresh(); }
    catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div className="grid">
      <div className="c s12" style={{ padding: "10px 16px" }}><ClockBar compact /></div>
      <div className="c kpi s3"><div className="l">近 7 天校核值</div><div className="v">{C.kpi.n7}</div><div className="d">热电偶 {C.kpi.tc} · 红外 {C.kpi.ir} · 手工 {C.kpi.man}</div></div>
      <div className="c kpi s3 kc" role="button" tabIndex={0} onClick={() => { setSt("待审核"); setSrc(""); setPage(0); }}><div className="l">待审核<span className="more">›</span></div><div className="v" style={{ color: "var(--acc)" }}>{C.kpi.pending}</div><div className="d">手工录入与导入的数据</div></div>
      <div className="c kpi s3"><div className="l">平均到达延迟</div><div className="v">{C.kpi.latency == null ? "—" : `${C.kpi.latency} min`}</div><div className="d">从测量时刻到进入平台（巡检、手工）</div></div>
      <div className="c kpi s3"><div className="l">评价对象</div><div className="v" style={{ fontSize: 20 }}>{C.target.name.split(" · ")[1] || C.target.name}</div><div className="d">{C.target.pick === "avg" ? `红外按时刻平均；热电偶扣除 1# 管偏差 ${C.offset > 0 ? "+" : ""}${C.offset} ℃` : "只用出口段 1# 管"}</div></div>
      <div className="c s12"><h2>在线校核值来源 <span className="sub">投用后随回放时钟到达，用来计算预测偏差；所有配对都按测量时刻</span></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>来源</th><th>类型</th><th>获取方式</th><th>频率</th><th>时间对齐</th><th>位置映射</th><th>可信等级</th></tr></thead>
          <tbody>{C.sources.map((r) => <tr key={r[0]}><td><b>{r[0]}</b></td>{r.slice(1).map((x, i) => <td key={i}>{x}</td>)}</tr>)}</tbody></table></div></div>
      <div className="c s12"><h2>校核值记录 <span className="sub">近 7 天（模拟时间）；{C.instance ? `同时刻预测值来自在线实例 ${C.instance.furnace_name} · ${C.instance.model}` : "还没有在线实例，暂无预测值"}</span></h2>
        <div className="row" style={{ marginBottom: 8 }}><select aria-label="按来源筛选" value={src} onChange={(e) => { setSrc(e.target.value); setPage(0); }}><option value="">全部来源</option><option>热电偶</option><option>巡检红外测温</option><option>手工录入 / 导入</option></select>
          <select aria-label="按状态筛选" value={st} onChange={(e) => { setSt(e.target.value); setPage(0); }}><option value="">全部状态</option><option>有效</option><option>待审核</option><option>质量异常</option><option>已作废</option></select></div>
        <div className="scroll"><table className="stack"><thead><tr><th>测量时刻</th><th>位置</th><th className="r">校核值</th><th className="r">同时刻预测值</th><th className="r">偏差</th><th>来源</th><th>状态</th></tr></thead>
          <tbody>{C.rows.length ? C.rows.map((r, i) => (
            <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{r.t}</td><td style={{ whiteSpace: "nowrap" }}>{r.position}</td><td className="r">{r.y == null ? "—" : r.y.toFixed(1)}</td>
              <td className="r">{r.p == null ? (r.applicable === false ? <span className="note">不适用，未输出</span> : "—") : r.p.toFixed(1)}{r.backfill && <div className="note">补算</div>}</td>
              <td className="r" style={r.dev != null && Math.abs(r.dev) > 12 ? { color: "var(--bad)", fontWeight: 700 } : undefined}>{r.dev == null ? "—" : (r.dev > 0 ? "+" : "") + r.dev.toFixed(1)}</td>
              <td>{r.source}<div className="note">{r.note}</div></td>
              <td>{r.status === "待审核" && r.id ? <div className="row"><button className="pri" onClick={() => review(r.id!, "approve")}>通过</button><button onClick={() => review(r.id!, "void")}>作废</button></div>
                : <span className={"tag " + (ST[r.status] || "t-mute")}>{r.status}</span>}{r.status === "有效" && !r.counted && r.p == null && r.applicable == null ? null : null}</td></tr>))
            : <tr><td colSpan={7} className="note" style={{ textAlign: "center", padding: 20 }}>没有符合条件的记录</td></tr>}</tbody></table></div>
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}><span className="note">第 {C.page + 1} / {C.pages} 页 · 共 {C.total} 条</span><button disabled={C.page === 0} onClick={() => setPage(page - 1)}>上一页</button><button disabled={C.page >= C.pages - 1} onClick={() => setPage(page + 1)}>下一页</button></div>
        <p className="note" style={{ margin: "6px 0 0" }}>偏差 = 预测值 − 校核值，只有"有效"且处在适用范围内的配对计入平均偏差。测量时刻没有现成预测值时（例如实例创建之前、校核值晚到），用同一条计算链补算，标"补算"。</p></div>
      <div className="c s7"><h2>手工录入 <span className="sub">提交后进入待审核，审核通过才参与偏差计算</span></h2>
        <div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr) 84px minmax(0,1fr)" }}>
          <span className="lab">测量时刻</span><input type="datetime-local" className="din" value={f.t} onChange={(e) => setF({ ...f, t: e.target.value })} />
          <span className="lab">测量位置</span><select value={f.pos} onChange={(e) => setF({ ...f, pos: e.target.value })}>{[1, 2, 3, 4, 5, 6, 7, 8].map((k) => <option key={k}>出口段 {k}# 管</option>)}</select>
          <span className="lab">校核值 {C.target.unit}</span><input type="text" inputMode="decimal" placeholder="例如 1048" value={f.v} onChange={(e) => setF({ ...f, v: e.target.value })} />
          <span className="lab">仪器</span><select value={f.inst} onChange={(e) => setF({ ...f, inst: e.target.value })}><option>红外高温计 IR-03</option><option>红外高温计 IR-05</option><option>热电偶（临时）</option></select>
          <span className="lab">测量人</span><input type="text" placeholder="姓名" value={f.who} onChange={(e) => setF({ ...f, who: e.target.value })} /></div>
        <p className="note err" style={{ minHeight: 18, margin: "6px 0" }}>{err}</p>
        <div className="row"><button className="pri" onClick={submit}>提交（待审核）</button><span className="note">合理范围 {C.target.lo}–{C.target.hi} {C.target.unit}；测量时刻不能晚于回放时钟</span></div></div>
      <div className="c s5"><h2>批量导入</h2><p className="note" style={{ margin: "0 0 8px" }}>按模板填写：测量时刻、测量位置、校核值、仪器、测量人</p>
        <div className="row"><label className="tag t-mute" style={{ padding: "8px 12px", cursor: "pointer", borderRadius: 7, fontSize: 14 }}>选择 Excel / CSV 文件<input type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }} onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} /></label>
          <a href="/api/calib-template.csv" download><button>下载导入模板</button></a></div><p className="note">{msg}</p></div>
    </div>
  );
}
