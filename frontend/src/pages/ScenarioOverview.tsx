import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";
import { api, ScenarioLite } from "../api";
import { EChart } from "../components/EChart";
import { FNS, go } from "../router";
import { tgLabel } from "../App";
import { cssVar, Modal, Spinner, useToast } from "../ui";

type Ov = { model: { code: string; published_at: string; mae: number | null; furnace: string } | null; stages: { name: string; state: string; info: string }[]; definition: [string, string][]; assets: [string, string][] };
type Mt = { clock: string; window: [string, string]; instance: { id: number; furnace: string; model: string; created: string } | null; val_mae?: number; mae?: number | null; n_pairs?: number; share?: number | null; minutes?: number; n_events?: number; bad_minutes?: number; big?: number; big_thr?: number };
const CAT: Record<string, [string, string, string]> = {
  range: ["参数超出了模型见过的范围", "#e67700", "与训练数据 P1–P99（或审核后的生效范围）比较"], regime: ["处在模型没学过的工况", "#868e96", "当前工况（实时判定）不在模型支持的工况列表中"],
  mahal: ["参数单看正常，但组合没见过", "#c92a2a", "多变量距离（马氏距离）超过阈值"], quality: ["输入数据质量异常", "#7048e8", "有输入列无效（缺失、冻结、超量程等）"], rule: ["人工补充规则", "#1c7ed6", "适用范围中的人工规则"],
};
const dur = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);

export default function ScenarioOverview({ sc }: { sc: ScenarioLite }) {
  const ov = useQuery({ queryKey: ["overview", sc.id], queryFn: () => api.get<Ov>(`/api/scenarios/${sc.id}/overview`) });
  const [thr, setThr] = useState(12);
  const mq = useQuery({ queryKey: ["metrics", sc.id, thr], queryFn: () => api.get<Mt>(`/api/scenarios/${sc.id}/metrics?thr=${thr}`), refetchInterval: 3000, placeholderData: (p) => p });
  const [dlg, setDlg] = useState("");
  const links = FNS.slice(1).map((f) => f[0]);
  const M = mq.data;
  const on = M?.instance;
  const kc = (k: string, l: string, v: React.ReactNode, d: string, color?: string) => (
    <button className="c kpi kc s3" onClick={() => setDlg(k)} disabled={k !== "model" ? !on : !ov.data?.model}><div className="l">{l}<span className="more">›</span></div><div className={"v" + (v === "—" ? " na" : "")} style={color ? { color } : undefined}>{v}</div><div className="d">{d}</div></button>);
  return (
    <div className="grid">
      {kc("model", "当前在线模型", on ? on.model : ov.data?.model ? ov.data.model.code : "—", on ? `${on.furnace} · ${on.created} 起在线` : ov.data?.model ? `${ov.data.model.published_at} 发布 · 待部署` : "尚未发布模型")}
      {kc("mae", "平均预测偏差（近 7 天）", on && M?.mae != null ? `${M.mae.toFixed(1)} ℃` : "—", on ? `检验时为 ${M?.val_mae} ℃ · ${M?.n_pairs} 个配对点（适用范围内、有效校核值）` : "部署在线实例并开始回放后计算")}
      {kc("valid", "适用范围内占比", on && M?.share != null ? `${M.share.toFixed(1)}%` : "—", on ? `${M?.n_events} 段时间不在适用范围，未输出（共 ${dur(M?.bad_minutes || 0)}）` : "部署在线实例并开始回放后计算")}
      {kc("big", "较大偏差次数（近 7 天）", on ? `${M?.big} 次` : "—", on ? `偏差超过 ${thr} ℃ 的校核点` : "部署在线实例并开始回放后计算", on ? "var(--acc)" : undefined)}
      {on && <p className="note s12" style={{ margin: "-6px 0 0" }}>指标按回放时钟计算：近 7 天 = {M?.window[0]} 至 {M?.window[1]}（在线实例 {on.created} 起运行）。</p>}
      <div className="c s12"><h2>建模流程 <span className="sub">各阶段可独立回退和重复执行</span></h2>
        {!ov.data ? <Spinner /> : <div className="pipe">{ov.data.stages.map((s, i) => (
          <a key={s.name} href={`#s${sc.id}-${links[i]}`} className={s.state === "done" ? "done" : s.state === "cur" ? "cur" : ""}><span className="note">{i + 1}</span><b>{s.name}</b><span className="note">{s.info}</span></a>))}</div>}
      </div>
      <div className="c s7"><h2>场景定义</h2><table><tbody>
        <tr><td>估算目标</td><td className="r">{tgLabel(sc.target)}</td></tr>
        {ov.data?.definition.map((a) => <tr key={a[0]}><td>{a[0]}</td><td className="r">{a[1]}</td></tr>)}</tbody></table></div>
      <div className="c s5"><h2>场景资产</h2><table><tbody>{ov.data?.assets.map((a) => <tr key={a[0]}><td>{a[0]}</td><td className="r">{a[1]}</td></tr>)}</tbody></table></div>
      <Modal open={!!dlg} onClose={() => setDlg("")} wide>
        <h2>{({ model: "当前在线模型", mae: "预测偏差明细", valid: "适用性明细 · 近 7 天", big: "较大偏差明细" } as Record<string, string>)[dlg]}<button style={{ marginLeft: "auto" }} onClick={() => setDlg("")}>关闭</button></h2>
        {dlg === "model" && <ModelD sid={sc.id} />}{dlg === "mae" && <MaeD sid={sc.id} />}{dlg === "valid" && <ValidD sid={sc.id} />}{dlg === "big" && <BigD sid={sc.id} thr={thr} setThr={setThr} />}
      </Modal>
    </div>
  );
}

function ModelD({ sid }: { sid: number }) {
  const q = useQuery({ queryKey: ["mdetail", sid], queryFn: () => api.get<any>(`/api/scenarios/${sid}/metrics/model`) });
  const d = q.data;
  if (!d) return <Spinner />;
  if (!d.code) return <p className="note">还没有已发布的模型。</p>;
  const MODE: Record<string, string> = { online: "在线", shadow: "影子测试", stopped: "已停用" };
  return (<>
    <div className="mk"><div>算法<b>{d.algo}</b></div><div>检验时偏差<b>{d.val_mae} ℃</b></div><div>发布时间<b style={{ fontSize: 16 }}>{d.published}</b></div><div>建模方式<b style={{ fontSize: 16 }}>{d.mode}</b></div></div>
    <h3>模型溯源</h3><table><tbody>
      <tr><td>来源实验</td><td>{d.experiment}（{d.batch}）</td><td>数据版本</td><td>{d.dataset}</td></tr>
      <tr><td>处理规则</td><td>{d.rule_code}</td><td>工况方案</td><td>{d.wc_code}（支持：{d.regimes.join("、")}）</td></tr>
      <tr><td>输入量配置</td><td>{d.f_code}（{d.n_inputs} 个参数）</td><td>检验报告</td><td>{d.report_code}</td></tr>
      <tr><td>审批人</td><td>{d.approvers.join("、")}</td><td>适用范围</td><td>{d.appl.join("；")} …</td></tr></tbody></table>
    <h3>运行实例</h3><table className="stack"><thead><tr><th>实例</th><th>模型</th><th>模式</th><th className="r">周期</th><th>上线时间（模拟）</th></tr></thead>
      <tbody>{d.instances.length ? d.instances.map((i: any) => <tr key={i.id}><td>{i.furnace}</td><td>{i.model}</td><td><span className={"tag " + (i.mode === "online" ? "t-ok" : i.mode === "shadow" ? "t-teal" : "t-mute")}>{MODE[i.mode]}</span></td><td className="r">{i.period} s</td><td>{i.created}</td></tr>)
        : <tr><td colSpan={5} className="note">还没有部署，去"上线投用"新增运行实例</td></tr>}</tbody></table>
    <h3>版本历史</h3><table className="stack"><thead><tr><th>版本</th><th>状态</th><th>发布时间</th><th className="r">在线平均偏差</th><th>替换原因</th></tr></thead>
      <tbody>{d.history.map((h: any) => <tr key={h.code}><td><b>{h.code}</b></td><td><span className={"tag " + (h.life === "已发布" ? "t-ok" : "t-mute")}>{h.life}</span></td><td>{h.published}</td><td className="r">{h.online_mae == null ? "—" : h.online_mae + " ℃"}</td><td>{h.note}</td></tr>)}</tbody></table>
  </>);
}

function MaeD({ sid }: { sid: number }) {
  const q = useQuery({ queryKey: ["maed", sid], queryFn: () => api.get<any>(`/api/scenarios/${sid}/metrics/mae`) });
  const [day, setDay] = useState("");
  const [page, setPage] = useState(0);
  const d = q.data;
  const days: string[] = useMemo(() => (d ? Array.from(new Set<string>(d.curve.map((c: any) => c[0].slice(0, 10)))) : []), [d]);
  const rows = (d?.rows || []).filter((r: any) => !day || r.t.startsWith(day)).slice().reverse();
  const cnt = rows.filter((r: any) => r.counted);
  const mae = cnt.length ? cnt.reduce((a: number, r: any) => a + Math.abs(r.dev), 0) / cnt.length : null;
  const opt = useMemo<EChartsOption | null>(() => {
    if (!d) return null;
    const cv = d.curve.filter((c: any) => !day || c[0].startsWith(day)), ink = cssVar("--ink"), bad = cssVar("--bad"), mute = cssVar("--mute");
    const pts = (d.rows as any[]).filter((r) => r.counted && (!day || r.t.startsWith(day)));
    return { animation: false, grid: { left: 50, right: 12, top: 12, bottom: 26 }, tooltip: { trigger: "axis", valueFormatter: (v: any) => (v == null ? "不输出" : (+v).toFixed(1)) },
      xAxis: { type: "time", axisLabel: { color: mute } }, yAxis: { type: "value", scale: true, axisLabel: { color: mute } },
      series: [{ type: "line", name: "预测值", data: cv.map((c: any) => [c[0], c[1]]), symbol: "none", lineStyle: { color: "#d9480f", width: 1.5 } },
        { type: "scatter", name: "校核值", data: pts.map((r) => ({ value: [r.t, r.y], itemStyle: { color: Math.abs(r.dev) > 12 ? bad : ink } })), symbolSize: 6 }] };
  }, [d, day]);
  if (!d) return <Spinner />;
  const pg = Math.max(1, Math.ceil(rows.length / 10)), p = Math.min(page, pg - 1);
  return (<>
    <div className="mk"><div>平均预测偏差<b>{mae == null ? "—" : mae.toFixed(1) + " ℃"}</b></div><div>检验时偏差<b>{d.val_mae} ℃</b></div><div>计入的校核点<b>{cnt.length}</b></div><div>最大偏差<b>{cnt.length ? Math.max(...cnt.map((r: any) => Math.abs(r.dev))).toFixed(1) + " ℃" : "—"}</b></div></div>
    <div className="row" style={{ marginBottom: 8 }}><button className={!day ? "pri" : ""} onClick={() => { setDay(""); setPage(0); }}>近 7 天</button>{days.map((x) => <button key={x} className={day === x ? "pri" : ""} onClick={() => { setDay(x); setPage(0); }}>{x.slice(5)}</button>)}</div>
    {opt && <EChart option={opt} height={240} />}
    <div className="legend"><span><i style={{ background: "#d9480f" }} />预测值（10 分钟均值；空缺处不适用、未输出）</span><span><i style={{ background: "var(--ink)", width: 8, height: 8, borderRadius: "50%" }} />校核值</span><span><i style={{ background: "var(--bad)", width: 8, height: 8, borderRadius: "50%" }} />偏差 &gt; 12 ℃</span></div>
    <h3>预测值与校核值对比</h3><div className="scroll"><table className="stack"><thead><tr><th>测量时刻</th><th className="r">预测值 ℃</th><th className="r">校核值 ℃</th><th className="r">偏差 ℃</th><th>校核来源</th><th>数据质量</th></tr></thead>
      <tbody>{rows.slice(p * 10, p * 10 + 10).map((r: any, i: number) => <tr key={i}><td>{r.t}</td><td className="r">{r.p == null ? "—" : r.p.toFixed(1)}{r.backfill && <span className="note"> 补算</span>}</td><td className="r">{r.y == null ? "—" : r.y.toFixed(1)}</td>
        <td className="r" style={r.dev != null && Math.abs(r.dev) > 12 ? { color: "var(--bad)", fontWeight: 700 } : undefined}>{r.dev == null ? "—" : (r.dev > 0 ? "+" : "") + r.dev.toFixed(1)}</td><td>{r.source}</td>
        <td>{r.counted ? <span className="tag t-ok">正常</span> : <span className="tag t-mute">{r.quality}</span>}</td></tr>)}</tbody></table></div>
    <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}><span className="note">第 {p + 1} / {pg} 页 · 共 {rows.length} 条</span><button disabled={p === 0} onClick={() => setPage(p - 1)}>上一页</button><button disabled={p >= pg - 1} onClick={() => setPage(p + 1)}>下一页</button></div>
  </>);
}

function ValidD({ sid }: { sid: number }) {
  const q = useQuery({ queryKey: ["validd", sid], queryFn: () => api.get<any>(`/api/scenarios/${sid}/metrics/valid`) });
  const [sel, setSel] = useState("");
  const d = q.data;
  if (!d) return <Spinner />;
  const bad = d.events.reduce((a: number, e: any) => a + e.minutes, 0);
  const longest = d.events.reduce((a: number, e: any) => Math.max(a, e.minutes), 0);
  const w0 = Date.parse(d.window[0].replace(" ", "T")), w1 = Date.parse(d.window[1].replace(" ", "T"));
  const pos = (t: string) => ((Date.parse(t.replace(" ", "T")) - w0) / (w1 - w0)) * 100;
  const evs = d.events.filter((e: any) => !sel || e.cat === sel);
  return (<>
    <p style={{ margin: "0 0 12px", fontSize: 15 }}>近 7 天，模型有 <b>{d.events.length} 段时间</b>（合计 <b>{dur(bad)}</b>，占 {d.minutes ? ((bad / d.minutes) * 100).toFixed(1) : 0}%）处在它没把握的情况下，这些时间模型没有输出结果。</p>
    <div className="mk"><div>适用占比<b>{d.share}%</b></div><div>不适用时长<b style={{ color: "var(--warn)" }}>{dur(bad)}</b></div><div>不适用次数<b>{d.events.length} 次</b></div><div>最长一次<b>{dur(longest)}</b></div></div>
    <h3>不适用的原因 <span className="note" style={{ fontWeight: 400 }}>点击可筛选下方事件</span></h3>
    <div className="why3">{d.cats.map((c: any) => { const m = CAT[c.cat] || [c.cat, "#888", ""]; return (
      <button key={c.cat} className={"wc3" + (sel === c.cat ? " on" : "")} onClick={() => setSel(sel === c.cat ? "" : c.cat)}><span className="dot" style={{ background: m[1] }} /><b>{m[0]}</b>
        <div className="big3">{c.n} 次 · 共 {dur(c.minutes)}</div><div className="note">例如：{c.examples.join("；")}</div><div className="note mt">判定方法：{m[2]}</div></button>); })}</div>
    <h3>发生在什么时候</h3><div className="strip">{d.events.map((e: any, i: number) => <i key={i} style={{ left: `${pos(e.start)}%`, width: `${Math.max(0.35, pos(e.end) - pos(e.start))}%`, background: (CAT[e.cat] || ["", "#888"])[1] }} title={`${e.start} ${e.detail}`} />)}</div>
    <div className="row note" style={{ justifyContent: "space-between", marginTop: 4 }}><span>{d.window[0]}</span><span>{d.window[1]}</span></div>
    <h3>每次的具体情况 {sel && <span className="note" style={{ fontWeight: 400 }}>· 仅显示"{CAT[sel]?.[0]}"（再次点击取消）</span>}</h3>
    <div className="scroll"><table className="stack"><thead><tr><th>时间</th><th className="r">不输出</th><th>原因</th><th>具体情况</th></tr></thead>
      <tbody>{evs.map((e: any, i: number) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{e.start.slice(5)} – {e.end.slice(11)}</td><td className="r">{e.minutes} min</td><td><span className="dot" style={{ background: (CAT[e.cat] || ["", "#888"])[1] }} />{(CAT[e.cat] || [e.cat])[0]}</td><td>{e.detail}</td></tr>)}</tbody></table></div>
    <p className="note">逐分钟判定，不适用的每一分钟都不输出；统计时把间隔不超过 15 分钟的不适用合并为一次。</p>
  </>);
}

function BigD({ sid, thr, setThr }: { sid: number; thr: number; setThr: (t: number) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["bigd", sid, thr], queryFn: () => api.get<any>(`/api/scenarios/${sid}/metrics/big?thr=${thr}`) });
  const d = q.data;
  const opt = useMemo<EChartsOption | null>(() => {
    if (!d) return null;
    const mute = cssVar("--mute"), teal = cssVar("--teal"), bad = cssVar("--bad"), lim = 22;
    return { animation: false, grid: { left: 44, right: 12, top: 10, bottom: 26 }, tooltip: { trigger: "item", formatter: (p: any) => `${p.value[0]}<br/>偏差 ${(+p.value[1]).toFixed(1)} ℃` },
      xAxis: { type: "time", min: d.window[0], max: d.window[1], axisLabel: { color: mute } }, yAxis: { type: "value", min: -lim, max: lim, axisLabel: { color: mute } },
      series: [{ type: "scatter", data: d.points.map((p: any) => ({ value: [p.t, Math.max(-lim, Math.min(lim, p.dev))], symbolSize: p.big ? 9 : 5, itemStyle: { color: p.big ? bad : teal, opacity: p.big ? 1 : 0.75 } })),
        markArea: { silent: true, data: [[{ yAxis: -thr, itemStyle: { color: cssVar("--oks"), opacity: 0.8 } }, { yAxis: thr }], ...d.episodes.map((e: any) => [{ xAxis: e.start, itemStyle: { color: mute, opacity: 0.18 } }, { xAxis: e.end }])] as any },
        markLine: { silent: true, symbol: "none", lineStyle: { color: "#d9480f", type: "dashed" }, label: { show: false }, data: [{ yAxis: thr }, { yAxis: -thr }] } }] };
  }, [d, thr]);
  if (!d) return <Spinner />;
  const big = d.points.filter((p: any) => p.big).slice().reverse();
  const mark = async (t: string, action: string) => { await api.post(`/api/scenarios/${sid}/metrics/big/mark`, { t, action }); qc.invalidateQueries({ queryKey: ["bigd", sid] }); if (action !== "待处理") toast(action === "加入训练集" ? "已加入待训练数据（跟踪改进的样本库中可见），下次生成数据版本时使用" : "已忽略"); };
  return (<>
    <p style={{ margin: "0 0 12px", fontSize: 15 }}>近 7 天模型在适用范围内共有 <b>{d.n}</b> 个校核点，其中 <b>{d.big} 个</b>偏差超过 {thr} ℃。</p>
    <div className="mk"><div>较大偏差次数<b style={{ color: "var(--acc)" }}>{d.big} 次</b></div><div>占校核点<b>{d.n ? ((d.big / d.n) * 100).toFixed(1) : 0}%</b></div><div>最大偏差<b>{d.max.toFixed(1)} ℃</b></div>
      <div>判定阈值<b><select aria-label="偏差阈值" value={thr} onChange={(e) => setThr(+e.target.value)} style={{ fontSize: 18, fontWeight: 700, padding: "2px 6px" }}>{[8, 10, 12, 15].map((t) => <option key={t}>{t}</option>)}</select> ℃</b></div></div>
    <h3>每个校核点的偏差</h3>{opt && <EChart option={opt} height={240} />}
    <div className="legend"><span><i style={{ background: "var(--teal)", width: 8, height: 8, borderRadius: "50%" }} />正常</span><span><i style={{ background: "var(--bad)", width: 8, height: 8, borderRadius: "50%" }} />偏差超过阈值</span><span><i style={{ background: "var(--oks)", height: 10 }} />阈值以内</span><span><i style={{ background: "var(--mute)", opacity: 0.35, height: 10 }} />不在适用范围，未输出</span></div>
    <h3>偏差较大的时间点</h3><div className="scroll"><table className="stack"><thead><tr><th>时间</th><th className="r">预测值 ℃</th><th className="r">校核值 ℃</th><th className="r">偏差 ℃</th><th>来源</th><th>处理</th></tr></thead>
      <tbody>{big.length ? big.map((r: any) => <tr key={r.t}><td>{r.t}</td><td className="r">{r.p.toFixed(1)}</td><td className="r">{r.y.toFixed(1)}</td><td className="r" style={{ color: "var(--bad)", fontWeight: 700 }}>{r.dev > 0 ? "+" : ""}{r.dev.toFixed(1)}</td><td>{r.source}</td>
        <td>{r.mark === "待处理" ? <div className="row"><button className="pri" onClick={() => mark(r.t, "加入训练集")}>加入训练集</button><button onClick={() => mark(r.t, "忽略")}>忽略</button></div>
          : <><span className={"tag " + (r.mark === "已入训练集" ? "t-ok" : "t-mute")}>{r.mark === "已入训练集" ? "加入训练集" : "忽略"}</span> <button className="note" style={{ minHeight: 28, padding: "2px 8px" }} onClick={() => mark(r.t, "待处理")}>撤销</button></>}</td></tr>)
        : <tr><td colSpan={6} className="note" style={{ textAlign: "center", padding: 20 }}>没有超过阈值的校核点</td></tr>}</tbody></table></div>
  </>);
}
