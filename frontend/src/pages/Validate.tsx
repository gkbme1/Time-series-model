import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { EChart } from "../components/EChart";
import { go } from "../router";
import { cssVar, Spinner, useToast } from "../ui";

type Check = { name: string; state: "ok" | "warn" | "bad" | "todo"; text: string };
type Var = { col: string; name: string; unit: string; p1: number; p99: number; lo: number; hi: number; why: string; dec: number; hmin: number; hmax: number; hist: number[] };
type Appl = { vars: Var[]; regimes: { id: string; name: string; n_train: number; mae: number | null; include: boolean; suggest: string }[];
  mahal: { method: string; pct: number; thr: number; thr_by_pct: Record<string, number>; train_d: number[] }; rules: any[]; reviewed: boolean };
type Cand = {
  id: number; code: string; exp: string; algo: string; mode: string; life: string; status: string; error: string; ds: string; rule: string; wc: string; f: string; batch: string;
  report_code: string; train_mae: number; published_at: string | null; history: { t: string; life: string; by: string; note: string }[];
  report?: any; appl?: Appl; checks?: Check[]; conclusion?: string; resp?: any[]; response_cfg?: any[]; lives?: string[]; tag_names?: Record<string, string>; rule_texts?: string[];
};
type VP = { cands: Cand[]; current: Cand | null; online: string | null };
type Prev = { share: number | null; mae_in: number | null; mae_out: number | null; n_in: number; n_out: number; reasons_hours: Record<string, number>; mahal_out_pct: number | null;
  replay_d_hist: number[]; train_d_hist: number[]; vars: { col: string; out_pct: number }[]; adjusted: number; invalid: string | null };

const CST: Record<string, [string, string]> = { ok: ["t-ok", "通过"], warn: ["t-warn", "注意"], bad: ["t-bad", "未通过"], todo: ["t-acc", "待人工处理"] };
const RN: Record<string, string> = { quality: "输入质量异常", range: "超出范围", regime: "没学过的工况", mahal: "组合异常", rule: "人工规则" };
const f1 = (v: any) => (v == null ? "—" : (+v).toFixed(1));

export default function ValidatePage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [cid, setCid] = useState<number | null>(null);
  const vq = useQuery({
    queryKey: ["validate", sid, cid], queryFn: () => api.get<VP>(`/api/scenarios/${sid}/validate` + (cid ? `?cid=${cid}` : "")),
    refetchInterval: (q) => (q.state.data?.current && ["排队中", "检验中"].includes(q.state.data.current.status) ? 1500 : false),
  });
  const V = vq.data;
  const C = V?.current;
  const [appl, setAppl] = useState<Appl | null>(null);
  const [rEdit, setREdit] = useState(false);
  const [err, setErr] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (C?.appl && C.status === "已完成") { setAppl(JSON.parse(JSON.stringify(C.appl))); setDirty(false); } else setAppl(null); setErr(""); }, [C?.id, C?.status, C?.life]);
  const [prev, setPrev] = useState<Prev | null>(null);
  const t = useRef<number>();
  useEffect(() => {
    if (!appl || !C) return;
    clearTimeout(t.current);
    t.current = window.setTimeout(async () => { try { setPrev(await api.post<Prev>(`/api/candidates/${C.id}/appl/preview`, { appl })); } catch {} }, 350);
  }, [appl, C?.id]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["validate", sid] }); qc.invalidateQueries({ queryKey: ["projects"] }); qc.invalidateQueries({ queryKey: ["batches", sid] }); qc.invalidateQueries({ queryKey: ["overview", sid] }); };

  if (!V) return <Spinner text="加载中…" />;
  if (!C) return <div className="c empty"><h2>还没有候选模型</h2><p>在"训练模型"的结果对比中选一个实验，点"送去检验效果"。</p><button className="pri" onClick={() => go(`s${sid}-lab`)}>去训练模型</button></div>;
  const R = C.report;
  const lock = C.life === "已发布" || C.life === "已归档";
  const ref = R?.rep?.online;
  const setA = (f: (a: Appl) => void) => { const a = JSON.parse(JSON.stringify(appl)); f(a); setAppl(a); setDirty(true); };
  const approve = async () => {
    setErr("");
    try { await api.post(`/api/candidates/${C.id}/approve`, { appl }); toast(`${C.code} 已发布，可在"上线投用"中部署（先以影子模式运行）`); refresh(); }
    catch (e) { setErr((e as Error).message); document.getElementById("vdCard")?.scrollIntoView({ behavior: "smooth" }); }
  };
  const reject = async () => { try { await api.post(`/api/candidates/${C.id}/reject`, { note: "" }); toast("已退回修改"); refresh(); } catch (e) { setErr((e as Error).message); } };
  const saveDraft = async () => { try { await api.put(`/api/candidates/${C.id}/appl`, { appl }); setDirty(false); toast("适用范围草稿已保存"); refresh(); } catch (e) { toast((e as Error).message); } };
  const regen = async () => { await api.post(`/api/candidates/${C.id}/appl/regen`); toast(`已按 ${C.ds} 训练数据重新生成（重新检验）`); refresh(); };
  const editResp = async (change: string, expect: string) => { await api.put(`/api/scenarios/${sid}/response`, { items: [{ change, expect }] }); refresh(); };

  return (
    <div className="grid">
      <div className="c s12 vfc"><div className="row"><span className="note">正在检验</span>
        <select aria-label="候选模型" value={C.id} onChange={(e) => setCid(+e.target.value)}>{V.cands.map((x) => <option key={x.id} value={x.id}>{x.code} 候选（{x.exp}）· {x.life}</option>)}</select>
        <span className="note">{C.ds} · {C.rule} · {C.wc} · {C.f} · {C.mode} · {C.algo} · 训练时偏差 {f1(C.train_mae)} ℃</span>
        <span className="row" style={{ marginLeft: "auto" }}><span className="note">对比对象</span>{ref ? <span className="tag t-ok">在线 {ref.code}</span> : <span className="tag t-mute">无在线模型 · 与基线比较</span>}</span></div></div>

      <div className="c s12"><h2>检验结果 <span className="sub">候选送来后自动完成全部检查；需要人处理的只有适用范围审核和审批</span></h2>
        {C.status !== "已完成" ? <p className="note">{C.status === "失败" ? <span className="err">检验失败：{C.error.split("\n")[0]}</span> : "正在自动检验（回放、分组统计、响应方向、生成适用范围）…"}</p>
          : <div className="vfl">{C.checks!.map((k, i) => (
            <button key={k.name} className={"vfi" + (k.state === "todo" || k.state === "bad" ? " att" : "")} onClick={() => document.getElementById(i === 2 ? "vdCard" : "vs" + (i + 1))?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              <span className="sn">{i + 1}</span><b>{k.name}</b><span className={"tag " + CST[k.state][0]}>{CST[k.state][1]}</span><div className="note">{k.text}</div></button>))}</div>}</div>

      {C.status === "已完成" && R && appl && <>
        <Replay R={R} C={C} />
        <div className="c s12" id="vs2"><h2><span className="sn">2</span>响应方向 <span className="sub">单独改变一个输入，估计值的变化方向是否符合工艺常识</span>
          <span className="tag t-mute" style={{ marginLeft: "auto" }}>自动运行</span><button onClick={() => setREdit(!rEdit)}>{rEdit ? "完成" : "编辑期望方向"}</button></h2>
          <div className="scroll"><table className="stack"><thead><tr><th>改变</th><th>工艺上应该</th><th className="r">模型给出（平均变化）</th><th>结果</th></tr></thead>
            <tbody>{C.resp!.map((r) => (<tr key={r.change}><td>{r.label}</td>
              <td>{rEdit ? <select aria-label="期望方向" value={r.expect} onChange={(e) => editResp(r.change, e.target.value)}><option value="up">↑ 上升</option><option value="down">↓ 下降</option></select> : `${r.expect === "up" ? "↑" : "↓"} ${r.expect_label}`}</td>
              <td className="r">{r.value == null ? "—" : `${r.value > 0 ? "+" : ""}${r.value.toFixed(2)} ℃`}</td>
              <td>{r.ok == null ? <span className="tag t-mute">{r.msg || "未测试"}</span> : <span className={"tag " + (r.ok ? "t-ok" : "t-bad")}>{r.ok ? "符合" : "方向相反"}</span>}</td></tr>))}</tbody></table></div>
          <p className="note" style={{ margin: "8px 0 0" }}>期望方向来自场景模板"裂解炉 · 炉管壁温"，由工艺人员确认。做法：在回放数据上把该输入的原始序列改变规定幅度，重新走同一条计算链（处理规则 → 工况 → 加工输入），其他输入不变；"本周期运行天数"直接加 10 天。{C.resp!.some((r) => r.ok === false) ? "方向相反说明模型学到的关系不符合工艺规律，不能发布，需回到训练调整。" : ""}</p></div>

        <ApplCard C={C} appl={appl} prev={prev} lock={lock} dirty={dirty} setA={setA} onSave={saveDraft} onRegen={regen} />

        <div className="c s12" id="vs4"><h2><span className="sn">4</span>结论与审批</h2>
          <table><tbody>
            <tr><td>平均偏差（回放 {R.rep.period[0]} 至 {R.rep.period[1]}）</td><td className="r">{f1(R.rep.mae)} ℃</td><td className="r note">{ref ? `在线 ${f1(ref.mae)} ℃` : `基线 ${f1(R.rep.base_mae)} ℃`}</td>
              <td className="r">{(() => { const b = ref ? ref.mae : R.rep.base_mae; return R.rep.mae < b ? <span className="tag t-ok">好 {(b - R.rep.mae).toFixed(1)} ℃</span> : <span className="tag t-warn">差 {(R.rep.mae - b).toFixed(1)} ℃</span>; })()}</td></tr>
            {R.seg_regime.filter((x: any) => x.id === "hi").map((x: any) => <tr key="hi"><td>高负荷偏差</td><td className="r">{x.mae == null ? "不输出" : f1(x.mae) + " ℃"}</td><td className="r note">{ref ? `在线 ${f1(x.online)} ℃` : ""}</td><td /></tr>)}
            <tr><td>最大偏差</td><td className="r">{f1(R.rep.max)} ℃</td><td className="r note">{ref ? `在线 ${f1(ref.max)} ℃` : ""}</td><td /></tr>
            <tr><td>适用范围内的偏差 / 适用占比</td><td className="r">{f1(prev?.mae_in)} ℃</td><td className="r note">适用占比 {prev?.share == null ? "—" : prev.share.toFixed(1) + "%"}</td><td /></tr></tbody></table>
          <p style={{ margin: "10px 0 0", fontSize: 14 }}><b>结论：</b>{C.conclusion}</p>
          <p className="note" style={{ margin: "8px 0 0" }}>检验报告 {C.report_code} 已自动生成（{R.created}），审批后随模型归档。</p>
          <h3 className="vh">审批</h3>
          <div className="flow">{C.lives!.map((n, i) => <span key={n} style={{ display: "contents" }}>{i > 0 && <em>→</em>}<span className={n === C.life ? "on" : ""}>{n}</span></span>)}<span className="note" style={{ border: 0, background: "none" }}>{C.code}（来自 {C.exp}）</span></div>
          <div className="row" style={{ marginTop: 12 }}><button className="pri" disabled={lock} onClick={approve}>审批通过并发布</button><button disabled={lock} onClick={reject}>退回修改</button>
            <span className="note">审批人：李工（工艺）+ 王工（负责人）· 适用范围随模型一起审批；发布时把模型、特征定义、处理规则、工况方案、适用范围打包成一个版本</span></div>
          <p className="note err" style={{ margin: "8px 0 0" }}>{err}</p>
          <details style={{ marginTop: 8 }}><summary className="note" style={{ cursor: "pointer" }}>状态记录（{C.history.length}）</summary>
            <table><tbody>{C.history.map((h, i) => <tr key={i}><td className="note">{h.t}</td><td>{h.life}</td><td>{h.by}</td><td className="note">{h.note}</td></tr>)}</tbody></table></details></div>
      </>}
    </div>
  );
}

function Replay({ R, C }: { R: any; C: Cand }) {
  const ref = R.rep.online;
  const opt = useMemo<EChartsOption>(() => {
    const ink = cssVar("--ink"), mute = cssVar("--mute"), ch = R.chart;
    const s: any[] = [
      { type: "line", data: ch.t.map((t: string, i: number) => [t, ch.lo[i]]), stack: "band", lineStyle: { opacity: 0 }, symbol: "none", silent: true, tooltip: { show: false } },
      { type: "line", data: ch.t.map((t: string, i: number) => [t, ch.lo[i] == null || ch.hi[i] == null ? null : ch.hi[i] - ch.lo[i]]), stack: "band", lineStyle: { opacity: 0 }, symbol: "none", areaStyle: { color: "#d9480f", opacity: 0.16 }, silent: true, tooltip: { show: false } },
      { type: "line", name: "候选 " + C.code, data: ch.t.map((t: string, i: number) => [t, ch.p[i]]), symbol: "none", lineStyle: { color: "#d9480f", width: 1.4 }, itemStyle: { color: "#d9480f" } },
      { type: "scatter", name: "校核值", data: ch.points, symbolSize: 5, itemStyle: { color: ink } },
    ];
    if (ch.online) s.push({ type: "line", name: "在线 " + ref.code, data: ch.t.map((t: string, i: number) => [t, ch.online[i]]), symbol: "none", lineStyle: { color: "#868e96", width: 1.1, type: "dashed" } });
    return { animation: false, grid: { left: 52, right: 14, top: 14, bottom: 46 }, tooltip: { trigger: "axis", confine: true, valueFormatter: (v: any) => (v == null ? "—" : (+v).toFixed(1)) },
      xAxis: { type: "time", axisLabel: { color: mute } }, yAxis: { type: "value", scale: true, axisLabel: { color: mute } }, series: s,
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 6 }] };
  }, [R]);
  const mx = Math.max(1, ...R.hist);
  const cov = R.coverage;
  const segMax = Math.max(1, ...R.seg_regime.map((x: any) => x.mae || 0), ...R.seg_cycle.map((x: any) => x.mae || 0));
  const row = (n: string, a: number | null, b: number | null, k: string) => (
    <tr key={k}><td>{n}</td><td className="r"><b>{a == null ? "不输出" : a.toFixed(1)}</b></td><td className="r">{ref ? (b == null ? "不输出" : b.toFixed(1)) : "—"}</td>
      <td>{a == null ? null : <div className="bar"><b style={{ width: `${(a / segMax) * 100}%`, background: ref && b != null && a > b ? "var(--warn)" : "var(--teal)" }} /></div>}</td></tr>);
  return (
    <div className="c s12" id="vs1"><h2><span className="sn">1</span>回放结果 <span className="sub">周期 {R.rep.test_cycles.join("、")}（检验集）{R.rep.cycles.filter((c: number) => !R.rep.test_cycles.includes(c)).map((c: number) => ` + 周期 ${c}（训练时未用）`)}{ref ? "，与在线模型并排比较" : ""}</span><span className="tag t-mute" style={{ marginLeft: "auto" }}>自动</span></h2>
      <div className="mk"><div>候选平均偏差<b>{f1(R.rep.mae)} ℃</b></div><div>{ref ? `在线 ${ref.code} 同期` : "基线同期"}<b>{f1(ref ? ref.mae : R.rep.base_mae)} ℃</b></div>
        <div>候选最大偏差<b>{f1(R.rep.max)} ℃</b></div><div>回放时段<b style={{ fontSize: 16 }}>{R.rep.period[0].slice(5)} 至 {R.rep.period[1].slice(5)}</b></div></div>
      <EChart option={opt} height={300} />
      <div className="legend"><span><i style={{ background: "var(--ink)", width: 8, height: 8, borderRadius: "50%" }} />校核值（红外测温，按时刻平均）</span><span><i style={{ background: "#d9480f" }} />候选模型（每小时）</span>
        <span><i style={{ background: "#d9480f", opacity: 0.25, height: 8 }} />候选 P10–P90 区间</span>{ref && <span><i style={{ background: "#868e96" }} />在线 {ref.code}</span>}<span className="note">曲线空缺处为输入无效或不在支持的工况（不输出）</span></div>
      <div className="grid" style={{ marginTop: 6 }}><div className="s7"><h3 className="vh">按工况、按周期 <span className="sub">偏差 ℃，校核样本数见括号</span></h3>
        <div className="scroll"><table className="stack"><thead><tr><th>分组</th><th className="r">候选 ℃</th><th className="r">{ref ? `在线 ${ref.code} ℃` : "在线"}</th><th style={{ width: "30%" }}>候选偏差</th></tr></thead>
          <tbody><tr><td colSpan={4} className="note">按工况</td></tr>{R.seg_regime.map((x: any) => row(`${x.name}（${x.n}）${x.include ? "" : " · 未纳入"}`, x.mae, x.online, "r" + x.id))}
            <tr><td colSpan={4} className="note">按周期</td></tr>{R.seg_cycle.map((x: any) => row(`周期 ${x.cycle}（${x.test ? "检验集" : "训练时未用"}，${x.n}）`, x.mae, x.online, "c" + x.cycle))}</tbody></table></div></div>
        <div className="s5"><h3 className="vh">误差分布 <span className="sub">候选 − 校核值，℃</span></h3><div className="hist">{R.hist.map((b: number, i: number) => <div key={i} title={`${-20 + i * 2} ~ ${-18 + i * 2} ℃：${b} 个`} style={{ height: `${(b / mx) * 100}%` }} />)}</div>
          <div className="row note" style={{ justifyContent: "space-between" }}><span>-20</span><span>0</span><span>+20</span></div>
          {cov != null && <div style={{ marginTop: 12 }}><div className="mk" style={{ gridTemplateColumns: "repeat(2,minmax(0,1fr))" }}><div>区间目标覆盖率<b>80%</b></div><div>实际覆盖率<b style={{ color: Math.abs(cov - 80) <= 5 ? "var(--ok)" : "var(--warn)" }}>{cov.toFixed(1)}%</b></div></div>
            <p className="note" style={{ margin: 0 }}>{Math.abs(cov - 80) <= 5 ? "区间可信：上线后显示的\"可信范围\"和实际情况基本一致。" : cov > 85 ? "区间偏宽：上线后显示的可信范围比实际保守，可以接受，但不够精细。" : "区间偏窄：上线后显示的可信范围会比实际乐观，建议重新校准。"}</p></div>}</div></div></div>
  );
}

function MiniHist({ v }: { v: Var }) {
  const W = 200, H = 36, n = v.hist.length, mx = Math.max(1, ...v.hist), X = (x: number) => ((x - v.hmin) / (v.hmax - v.hmin)) * W;
  return (<svg className="mh" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
    <rect x={X(v.p1)} y={0} width={Math.max(0, X(v.p99) - X(v.p1))} height={H} style={{ fill: "var(--teals)" }} />
    {v.hist.map((c, k) => { const h = (c / mx) * (H - 4); return <rect key={k} x={(k * W) / n + 0.6} y={H - h} width={W / n - 1.2} height={h} style={{ fill: "var(--teal)" }} opacity={0.75} />; })}
    {[v.lo, v.hi].map((x, k) => <rect key={k} x={Math.max(1, Math.min(W - 1, X(x))) - 1} y={0} width={2} height={H} fill="#d9480f" />)}</svg>);
}

function ApplCard({ C, appl, prev, lock, dirty, setA, onSave, onRegen }: { C: Cand; appl: Appl; prev: Prev | null; lock: boolean; dirty: boolean; setA: (f: (a: Appl) => void) => void; onSave: () => void; onRegen: () => void }) {
  const changed = (v: Var) => v.lo !== v.p1 || v.hi !== v.p99;
  const st = lock ? `已随 ${C.code} 发布` : appl.reviewed ? "已审核" : dirty ? "草稿（未保存）" : "草稿";
  const distOpt = useMemo<EChartsOption | null>(() => {
    if (!prev) return null;
    const mute = cssVar("--mute"), teal = cssVar("--teal"), thr = appl.mahal.thr;
    const xs = Array.from({ length: 40 }, (_, i) => (i * 0.2 + 0.1).toFixed(1));
    const tot = (a: number[]) => Math.max(1, a.reduce((x, y) => x + y, 0));
    return { animation: false, grid: { left: 36, right: 10, top: 18, bottom: 24 }, legend: { top: 0, right: 0, textStyle: { color: mute, fontSize: 11 }, itemHeight: 8 },
      tooltip: { trigger: "axis", valueFormatter: (v: any) => (+v).toFixed(1) + "%" },
      xAxis: { type: "category", data: xs, axisLabel: { color: mute, interval: 9 } }, yAxis: { type: "value", axisLabel: { color: mute, formatter: "{value}%" } },
      series: [{ name: "训练样本", type: "bar", data: prev.train_d_hist.map((c, i) => ({ value: (c / tot(prev.train_d_hist)) * 100, itemStyle: { color: +xs[i] >= thr ? "#c92a2a" : teal, opacity: 0.6 } })), barGap: "-100%" },
        { name: "回放时段", type: "line", data: prev.replay_d_hist.map((c) => (c / tot(prev.replay_d_hist)) * 100), symbol: "none", lineStyle: { color: "#d9480f" },
          markLine: { symbol: "none", label: { formatter: `阈值 ${thr}`, color: "#d9480f" }, lineStyle: { color: "#d9480f", width: 2 }, data: [{ xAxis: Math.min(39, Math.round((thr - 0.1) / 0.2)) }] } }] };
  }, [prev, appl.mahal.thr]);
  const outOf = (col: string) => prev?.vars.find((x) => x.col === col)?.out_pct;
  const ruleCtl = (r: any, i: number) => {
    const num = (k: string, w = 50) => <input type="text" inputMode="decimal" disabled={lock} style={{ width: w, minHeight: 30, padding: "2px 6px" }} value={r[k]} onChange={(e) => setA((a) => { a.rules[i][k] = e.target.value; })} />;
    if (r.type === "quality") return <span>关键输入数据质量异常（有输入列无效）时，判为不适用</span>;
    if (r.type === "after_event") return <span className="row" style={{ gap: 4 }}>烧焦或停炉结束、重新投料后 {num("hours")} 小时内，判为不适用</span>;
    return <span className="row" style={{ gap: 4 }}><select disabled={lock} value={r.tag} onChange={(e) => setA((a) => { a.rules[i].tag = e.target.value; })} style={{ minHeight: 30, fontSize: 13 }}>{Object.entries(C.tag_names || {}).map(([t, n]) => <option key={t} value={t}>{n}</option>)}</select>
      {num("minutes")} 分钟内变化超过 {num("pct")}% 时，判为不适用</span>;
  };
  return (
    <div className="c s12" id="vdCard"><span className="tag t-acc" style={{ float: "right" }}>需人工审核</span>
      <h2><span className="sn">3</span>适用范围 · {C.code} <span className={"tag " + (lock || appl.reviewed ? "t-ok" : "t-warn")}>{st}</span><span className="sub">由 {C.exp} 训练数据（{C.ds}）自动生成，审核后随模型发布，发布后不可单独修改</span>
        <span style={{ marginLeft: "auto" }} className="row"><button disabled={lock} onClick={onRegen}>从训练数据重新生成</button><button disabled={lock || !dirty} onClick={onSave}>保存草稿</button></span></h2>
      <div className="vimp"><div>适用占比（回放时段，按小时）<b>{prev?.share == null ? "…" : prev.share.toFixed(1) + "%"}</b></div><div>适用时段平均偏差<b>{f1(prev?.mae_in)} ℃</b></div>
        <div>不适用时段平均偏差<b>{f1(prev?.mae_out)} ℃</b></div><div>调整项<b>{prev?.adjusted ?? 0} 个变量</b></div>
        <div className="t">{prev && <>不适用的小时数：{Object.entries(prev.reasons_hours).filter(([, v]) => v > 0).map(([k, v]) => `${RN[k]} ${v} h`).join("、") || "无"}。
          {prev.mae_in != null && prev.mae_out != null ? (prev.mae_in < prev.mae_out ? " 当前设置能区分：适用时段偏差低于不适用时段。" : " 适用时段偏差不低于不适用时段，范围可能放得过宽。") : ""}
          {prev.share != null && prev.share < 85 ? " 适用占比偏低，在线时会频繁判为不适用；如果确认某些参数的新范围属于正常操作，可以放宽并写明依据。" : ""}
          {prev.invalid && <span className="err"> {prev.invalid}</span>}</>}</div></div>

      <h3 className="vh">1 单变量范围 <span className="sub">浅色为训练数据 P1–P99，橙色竖线为生效范围；超出生效范围即判为不适用。右侧为回放时段超出比例</span></h3>
      <div className="scroll"><table className="stack"><thead><tr><th>输入变量</th><th style={{ width: "24%" }}>训练数据分布</th><th className="r">自动范围（P1–P99）</th><th>生效范围</th><th className="r">回放超出</th><th>来源</th><th style={{ width: "22%" }}>调整说明</th></tr></thead>
        <tbody>{appl.vars.map((v, i) => { const c = changed(v), bad = v.lo >= v.hi; return (
          <tr key={v.col}><td><b>{v.name}</b>{v.unit && <div className="note">{v.unit}</div>}</td><td><MiniHist v={v} /><div className="row note" style={{ justifyContent: "space-between" }}><span>{v.hmin}</span><span>{v.hmax}</span></div></td>
            <td className="r">{v.p1} – {v.p99}</td>
            <td><div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>{(["lo", "hi"] as const).map((k, j) => <span key={k} style={{ display: "contents" }}>{j > 0 && <span>–</span>}
              <NumIn bad={bad} disabled={lock} v={v[k]} onCommit={(x) => setA((a) => { a.vars[i][k] = x; })} /></span>)}</div></td>
            <td className="r">{outOf(v.col) == null ? "—" : outOf(v.col)!.toFixed(1) + "%"}</td>
            <td>{c ? <><span className="tag t-warn">已调整</span><div><button className="note" disabled={lock} style={{ minHeight: 28, padding: "2px 8px", marginTop: 4 }} onClick={() => setA((a) => { a.vars[i].lo = v.p1; a.vars[i].hi = v.p99; a.vars[i].why = ""; })}>恢复自动</button></div></> : <span className="tag t-mute">自动</span>}</td>
            <td>{c ? <input className={"why" + (!v.why.trim() ? " bad" : "")} disabled={lock} value={v.why} placeholder="必填：调整依据" onChange={(e) => setA((a) => { a.vars[i].why = e.target.value; })} /> : <span className="note">—</span>}</td></tr>); })}</tbody></table></div>

      <div className="grid" style={{ marginTop: 6 }}>
        <div className="s6"><h3 className="vh">2 支持的工况 <span className="sub">当前工况（实时判定）不在列表中即判为不适用</span></h3>
          <table><thead><tr><th>纳入</th><th>工况</th><th className="r">训练样本</th><th className="r">回放偏差</th><th>建议</th></tr></thead>
            <tbody>{appl.regimes.map((r, i) => <tr key={r.id}><td><input type="checkbox" disabled={lock || !r.n_train} checked={r.include} aria-label={"纳入" + r.name} onChange={(e) => setA((a) => { a.regimes[i].include = e.target.checked; })} /></td>
              <td>{r.name}</td><td className="r">{r.n_train || "—"}</td><td className="r">{r.mae == null ? "—" : r.mae + " ℃"}</td><td>{r.suggest === "纳入" ? <span className="tag t-ok">纳入</span> : <span className="note">{r.suggest}</span>}</td></tr>)}</tbody></table></div>
        <div className="s6"><h3 className="vh">3 多变量组合检查 <span className="sub">单个变量都在范围内，但组合在训练中没出现过</span></h3>
          <div className="row" style={{ marginBottom: 6 }}><span className="note">方法</span><select aria-label="检测方法" disabled={lock}><option>马氏距离</option><option disabled>孤立森林（未实现）</option></select>
            <span className="note">阈值取训练样本距离的</span><select aria-label="阈值分位数" disabled={lock} value={String(appl.mahal.pct)} onChange={(e) => setA((a) => { a.mahal.pct = +e.target.value; a.mahal.thr = a.mahal.thr_by_pct[e.target.value]; })}>{Object.keys(appl.mahal.thr_by_pct).map((p) => <option key={p} value={p}>{p}</option>)}</select><span className="note">分位数</span></div>
          {distOpt && <EChart option={distOpt} height={160} />}
          <p className="note">阈值 {appl.mahal.thr}：训练样本中 {(100 - appl.mahal.pct).toFixed(1)}% 超出；回放时段中约 {prev?.mahal_out_pct ?? "…"}% 的时间超出（其中先被其他原因判为不适用的不重复计入原因统计）。</p></div></div>

      <h3 className="vh">4 人工补充规则</h3>
      {appl.rules.map((r, i) => <div className="rule" key={i}><label className="row" style={{ gap: 8, cursor: "pointer" }}><input type="checkbox" disabled={lock || r.type === "quality"} checked={r.on} onChange={(e) => setA((a) => { a.rules[i].on = e.target.checked; })} /></label>{ruleCtl(r, i)}
        {i > 2 && !lock && <button className="x" style={{ minHeight: 28, padding: "2px 8px" }} onClick={() => setA((a) => { a.rules.splice(i, 1); })}>删除</button>}</div>)}
      {!lock && <div className="row" style={{ marginTop: 6 }}><select aria-label="添加规则" value="" onChange={(e) => { const tp = e.target.value; if (!tp) return; setA((a) => { a.rules.push(tp === "rate" ? { type: "rate", tag: Object.keys(C.tag_names || {})[0], minutes: 5, pct: 15, on: true } : { type: "after_event", hours: 2, on: true }); }); }}>
        <option value="">添加规则…</option><option value="rate">某参数短时间变化过大</option><option value="after_event">重新投料后若干小时内</option></select></div>}
    </div>
  );
}

function NumIn({ v, onCommit, disabled, bad }: { v: number; onCommit: (x: number) => void; disabled?: boolean; bad?: boolean }) {
  const [s, setS] = useState(String(v));
  useEffect(() => setS(String(v)), [v]);
  return <input className={"rin" + (bad ? " bad" : "")} disabled={disabled} inputMode="decimal" value={s} onChange={(e) => setS(e.target.value)} onBlur={() => { const n = parseFloat(s); if (!isNaN(n)) onCommit(n); else setS(String(v)); }} />;
}
