import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../router";
import { fmtN, Spinner, useToast } from "../ui";

type Exp = { id: number; code: string; mode: string; algo: string; status: string; progress: number; stage: string; metrics: any; importance: { col: string; inc: number }[]; label: string; error: string; params: any };
type Batch = { id: number; code: string; ds: string; rule: string; wc: string; f: string; note: string; sig: string; time: string; exps: Exp[]; conclusion: string[] };
type Page = {
  empty?: boolean; cfg: any; datasets: { code: string; start: string; end: string; rule_code: string }[]; algs: Record<string, string>; modes: Record<string, string>;
  param_meta: Record<string, [string, string, string][]>; defaults: Record<string, any>;
  funnel: { label: string; v: number; unit: string; src: string }[]; preview: { cols: string[]; n_cols: number; target: string; unit: string; rows: { t: string; x: (number | null)[]; y: number }[] };
  per_regime: { id: string; name: string; n: number }[]; recommend_mode: string; min_share: { name: string; pct: number }; can_cycle: boolean;
  cycles: { i: number; start: string; end: string; days: number; done: boolean; role: string; n: number }[]; split_counts: Record<string, number>;
  test_by_regime: { name: string; n: number }[]; n_samples: number; monotone: { col: string; sign: number }[]; mono_names: string[]; n_exp: number; sig: string; batches: Batch[];
};

const MODE_DESC: Record<string, string> = { uni: "所有纳入的工况一起训练，把\"工况\"作为一个输入", per: "每个工况各训练一个模型，上线后按当前工况切换", main: "只用正常稳定的数据，其他工况不输出" };
const ALG_DESC: Record<string, [string, string[]]> = {
  base: ["直接沿用上一次的校核值，是比较的底线", ["必跑"]], lin: ["简单、好解释，关系接近直线时够用", ["可解释"]], rf: ["稳健，不容易被异常点带偏", ["稳"]],
  xgb: ["工业数据上通常最准，支持单调约束和分位数区间", ["主力", "推荐"]], lstm: ["直接看一段历史曲线，需要连续且大量的校核值", ["阶段 5（可选）"]],
};
const ROLE: [string, string][] = [["train", "训练"], ["test", "检验"], ["skip", "不用"]];
const f1 = (v: any) => (v == null ? "—" : (+v).toFixed(1));

export default function LabPage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const pq = useQuery({ queryKey: ["train", sid], queryFn: () => api.get<Page>(`/api/scenarios/${sid}/train`) });
  const P = pq.data;
  const running = !!P?.batches?.[0]?.exps.some((e) => e.status === "排队中" || e.status === "训练中");
  const bq = useQuery({
    queryKey: ["batches", sid], queryFn: () => api.get<{ batches: Batch[]; sig: string }>(`/api/scenarios/${sid}/train/batches`),
    enabled: !!P && !P.empty, refetchInterval: (q) => (q.state.data?.batches[0]?.exps.some((e) => e.status === "排队中" || e.status === "训练中") ? 1500 : false),
  });
  const B = bq.data?.batches ?? P?.batches ?? [];
  const cur = B[0];
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const live = !!cur?.exps.some((e) => e.status === "排队中" || e.status === "训练中");
  useEffect(() => { if (!live && running) { toast("训练完成"); } }, [live]);
  const put = async (body: object) => { try { qc.setQueryData(["train", sid], await api.put<Page>(`/api/scenarios/${sid}/train/config`, body)); } catch (e) { toast((e as Error).message); } };

  if (!P) return <Spinner text="加载中…" />;
  if (P.empty) return <div className="c empty"><h2>还没有数据版本</h2><p>请先在"选数据"中点"生成训练数据"，得到 DS-v1 后再来训练。</p><button className="pri" onClick={() => go(`s${sid}-data`)}>去选数据</button></div>;
  const c = P.cfg;
  const done = cur ? cur.exps.filter((e) => e.status === "已完成" && e.metrics?.mae != null) : [];
  const best = done.filter((e) => e.algo !== "base").sort((a, b) => a.metrics.mae - b.metrics.mae)[0];
  const selExp = done.find((e) => e.id === sel) || best;
  const stale = cur && bq.data && bq.data.sig !== cur.sig && !live;
  const fmax = P.funnel[0].v;
  const tot = Math.max(1, P.split_counts.train + P.split_counts.test + P.split_counts.skip);
  const run = async () => {
    setBusy(true);
    try { await api.post(`/api/scenarios/${sid}/train/run`); setSel(null); await qc.invalidateQueries({ queryKey: ["batches", sid] }); await qc.invalidateQueries({ queryKey: ["train", sid] }); qc.invalidateQueries({ queryKey: ["projects"] }); document.getElementById("lbCur")?.scrollIntoView({ behavior: "smooth", block: "start" }); }
    catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const send = async () => {
    if (!selExp) return;
    try { const r = await api.post<{ code: string }>(`/api/experiments/${selExp.id}/send`); toast(`${selExp.code} 已送去检验，候选版本 ${r.code}`); qc.invalidateQueries({ queryKey: ["projects"] }); go(`s${sid}-validate`); }
    catch (e) { toast((e as Error).message); }
  };
  const nAlg = c.algs.filter((a: string) => a !== "lstm");
  return (
    <div className="grid">
      <div className="c s12 stepc"><h2><span className="sn">1</span>训练数据 <span className="sub">确认前面几步的定义都落到了训练表上</span>
        <span className="row" style={{ marginLeft: "auto" }}><span className="note">数据版本</span><select aria-label="数据版本" value={c.ds} onChange={(e) => put({ ds: e.target.value })}>{P.datasets.map((d) => <option key={d.code} value={d.code}>{d.code} · {d.start} 至 {d.end} · {d.rule_code}</option>)}</select></span></h2>
        <div className="funnel">{P.funnel.map((f, i) => (
          <div className="fr" key={i}><span className="fl">{f.label}</span><div className="fb"><b style={{ width: `${Math.max(4, (i === 4 ? 1 : f.v / fmax) * 100)}%` }} /></div>
            <span className="fv">{fmtN(Math.round(f.v))} {f.unit}</span><span className="note fs">{f.src ? "来自 " + f.src : ""}</span></div>))}</div>
        <p className="note" style={{ margin: "6px 0 0" }}>{P.n_samples < 500 ? <span className="err">样本不足 500 条，训练结果可能不可靠，建议扩大时间范围或补充校核数据。</span> : "样本数够用。每一步的数字都可以回到对应页面修改；最后一步只统计纳入工况内、所有输入列都有效的校核样本。"}</p>
        <h3 className="vh">训练表预览 <span className="sub">共 {P.preview.n_cols} 个输入列 + 1 个校核值列，前 5 行（真实数据）</span></h3>
        <div className="scroll"><table className="pv"><thead><tr><th>时间</th>{P.preview.cols.map((x) => <th key={x}>{x}</th>)}<th>…</th><th className="tgtc">{P.preview.target}（校核值）</th></tr></thead>
          <tbody>{P.preview.rows.map((r) => <tr key={r.t}><td className="note">{r.t}</td>{r.x.map((v, j) => <td key={j} className="r">{v == null ? "—" : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)}</td>)}<td className="note">…</td><td className="r tgtc">{r.y.toFixed(1)}</td></tr>)}</tbody></table></div></div>

      <div className="c s12 stepc"><h2><span className="sn">2</span>建模方式 <span className="sub">要不要按工况分开训练</span></h2>
        <div className="mcards">{Object.entries(P.modes).map(([k, n]) => (
          <button key={k} className={"mcard" + (c.mode === k ? " on" : "")} onClick={() => put({ mode: k })}><b>{n}</b>{k === P.recommend_mode && <span className="tag t-teal">推荐</span>}
            <div className="note">{MODE_DESC[k]}</div>
            {k === "per" && <div className="note" style={{ marginTop: 6 }}>{P.per_regime.map((r) => `${r.name} ${fmtN(r.n)} 条`).join(" · ")}</div>}
            {k === "uni" && k === P.recommend_mode && <div className="note" style={{ marginTop: 6 }}>{P.min_share.name}只占 {P.min_share.pct}%，单独训练样本偏少</div>}</button>))}</div>
        <label className="row" style={{ gap: 8, marginTop: 10, cursor: "pointer" }}><input type="checkbox" checked={c.compare} onChange={(e) => put({ compare: e.target.checked })} /> 同时训练另一种方式做对比（推荐，按工况比较偏差后再定）</label>
        <h3 className="vh">训练 / 检验数据划分 <span className="sub">按时间顺序切，不随机抽样；调参也用按时间顺序的交叉验证</span></h3>
        <div className="row"><select aria-label="划分方式" value={c.split.type} onChange={(e) => put({ split: { type: e.target.value } })}><option value="cycle" disabled={!P.can_cycle}>按运行周期</option><option value="time">按时间：最后 20% 做检验</option></select>
          <span className="note">{c.split.type === "cycle" ? (P.split_counts.test ? "默认最后一个完整周期做检验，未结束的周期不用（检验效果时会拿它做补充回放）" : "至少需要一个周期做检验") : P.can_cycle ? "" : "完整周期不足 2 个，只能按时间划分"}</span></div>
        {c.split.type === "cycle" && <div className="cyrole">{P.cycles.map((y) => (
          <div className="cyc" key={y.i}><b>周期 {y.i}</b><span className="note">{y.start.slice(0, 10)} – {y.done ? y.end.slice(0, 10) : "至今"} · {fmtN(y.n)} 个样本{y.done ? "" : " · 未结束"}</span>
            <div className="seg sm" role="group">{ROLE.map(([k, n]) => <button key={k} className={y.role === k ? "on" : ""} onClick={() => put({ split: { roles: { [y.i]: k } } })}>{n}</button>)}</div></div>))}</div>}
        <div className="split" style={{ marginTop: 8 }}>
          <div style={{ flex: P.split_counts.train / tot, background: "#0d7680" }}>训练 {fmtN(P.split_counts.train)} 条{c.split.type === "cycle" ? " · 周期 " + P.cycles.filter((y) => y.role === "train").map((y) => y.i).join("、") : " · 前 80%"}</div>
          {P.split_counts.test > 0 && <div style={{ flex: P.split_counts.test / tot, background: "#d9480f" }}>检验 {fmtN(P.split_counts.test)} 条{c.split.type === "cycle" ? " · 周期 " + P.cycles.filter((y) => y.role === "test").map((y) => y.i).join("、") : " · 后 20%"}</div>}
          {P.split_counts.skip > 0 && <div style={{ flex: P.split_counts.skip / tot, background: "#adb5bd" }}>不用 {fmtN(P.split_counts.skip)} 条</div>}</div>
        <div style={{ marginTop: 8 }}>{P.test_by_regime.map((r) => <span key={r.name} className={"tag " + (r.n < 30 ? "t-warn" : "t-ok")} style={{ marginRight: 6 }}>检验集中「{r.name}」{r.n} 条{r.n < 30 ? "，偏少" : ""}</span>)}</div></div>

      <div className="c s12 stepc"><h2><span className="sn">3</span>算法与参数</h2>
        <div className="lagHint ok">样本 {fmtN(P.n_samples)} 条，校核值以红外测温为主（每班一次，较稀疏）→ 推荐：基线、线性回归、随机森林、XGBoost；不推荐 LSTM（需要连续且大量的校核值，放在阶段 5 可选）。</div>
        <div className="acards">{Object.entries(P.algs).map(([k, n]) => (
          <label key={k} className={"acard" + (c.algs.includes(k) ? " on" : "")} style={k === "lstm" ? { opacity: 0.55, cursor: "not-allowed" } : undefined}>
            <input type="checkbox" checked={c.algs.includes(k)} disabled={k === "base" || k === "lstm"} onChange={(e) => put({ algs: e.target.checked ? [...nAlg, k] : nAlg.filter((a: string) => a !== k) })} />
            <span><b>{n}</b> {ALG_DESC[k][1].map((t) => <span key={t} className={"tag " + (t === "推荐" ? "t-teal" : t.startsWith("阶段") ? "t-warn" : "t-mute")} style={{ marginLeft: 4 }}>{t}</span>)}<div className="note">{ALG_DESC[k][0]}</div></span></label>))}</div>
        <h3 className="vh">参数怎么定</h3>
        <div className="seg" role="group" aria-label="参数方式">{[["def", "默认值"], ["auto", "自动调参"], ["man", "手动设置"]].map(([k, n]) => <button key={k} className={c.pm === k ? "on" : ""} onClick={() => put({ pm: k })}>{n}</button>)}</div>
        <div style={{ marginTop: 10 }}>
          {c.pm === "def" && <p className="note" style={{ margin: 0 }}>平台按算法给出默认参数，直接可用。大多数情况选这个就够了。</p>}
          {c.pm === "auto" && <div className="row"><span className="note">搜索预算</span><select value={c.budget} onChange={(e) => put({ budget: +e.target.value })}>{[20, 40, 80].map((b) => <option key={b} value={b}>{b} 轮</option>)}</select>
            <span className="note">Optuna 搜索；评估方式：训练集内按时间顺序切分交叉验证（TimeSeriesSplit，不随机抽样，避免相邻时刻泄漏）</span></div>}
          {c.pm === "man" && (nAlg.filter((a: string) => P.param_meta[a]).map((a: string) => (
            <div className="mp" key={a}><b>{P.algs[a]}</b>{P.param_meta[a].map(([k, n, hlp]) => (
              <label key={k} className="mpi"><span>{n}</span><ManInput v={c.manual?.[a]?.[k] ?? P.defaults[a][k]} onCommit={(v) => put({ manual: { [a]: { [k]: v } } })} /><span className="note">{hlp}</span></label>))}</div>)))}
        </div>
        <h3 className="vh">其他</h3>
        <label className="row" style={{ gap: 8, cursor: "pointer" }}><input type="checkbox" checked={c.interval} onChange={(e) => put({ interval: e.target.checked })} /> 同时输出预测区间（P10–P90），上线后显示估计值的可信范围</label>
        {c.algs.includes("xgb") && <label className="row" style={{ gap: 8, cursor: "pointer", marginTop: 8 }}><input type="checkbox" checked={c.monotone} disabled={!P.monotone.length} onChange={(e) => put({ monotone: e.target.checked })} />
          单调约束（XGBoost，来自场景模板）：{P.mono_names.join("、")}增加时，壁温估计值不能下降 <span className="note">（作用于 {P.monotone.map((m) => m.col).join("、") || "—"}）</span></label>}
        <div className="row" style={{ marginTop: 14 }}><span className="note">将训练 {P.n_exp} 个实验：{nAlg.map((a: string) => P.algs[a]).join("、")}{c.compare && c.mode !== "main" ? " × 统一 / 分工况" : ""}{c.pm === "auto" ? ` · 自动调参 ${c.budget} 轮` : ""}；同时最多 3 个并行</span>
          <button className="pri" style={{ marginLeft: "auto" }} disabled={busy || live} onClick={run}>{live ? "训练中…" : "开始训练"}</button></div></div>

      <div className="c s12 stepc" id="lbCur"><h2><span className="sn">4</span>结果对比 <span className="sub">训练完成后，在检验数据上比较，选出最好的送去"检验效果"</span></h2>
        {stale && <div className="lagHint warn" style={{ marginBottom: 10 }}><b>配置已改动</b>：下面的结果基于 {cur.time} 训练时的配置（{cur.ds}、{cur.rule}、{cur.wc}、{cur.f}），与当前上面三步或前面页面的设置不一致。需要的话请重新训练。</div>}
        {!cur ? <div className="empty" style={{ padding: "28px 12px" }}><h2 style={{ fontSize: 16 }}>还没有训练结果</h2><p style={{ margin: 0 }}>完成上面三步后，点"开始训练"。</p></div> : <>
          <div className="row" style={{ marginBottom: 8 }}><b>{cur.code}</b><span className="note">{cur.time} · {cur.ds} · {cur.rule} · {cur.wc} · {cur.f} · {cur.note}</span>
            {live ? <><span className="tag t-teal">已完成 {cur.exps.filter((e) => e.status === "已完成").length} / {cur.exps.length}</span><span className="note">可以离开此页，后台继续训练</span></> : <span className="tag t-ok">全部完成</span>}</div>
          <ExpTable exps={cur.exps} sel={selExp?.id} onSel={setSel} pick />
          {done.length > 0 && <div className="grid" style={{ marginTop: 12 }}>
            <div className="s7"><h3 className="vh">输入重要性 <span className="sub">{selExp ? `${selExp.code} · ${P.algs[selExp.algo]} · 在检验集上逐列打乱后偏差增加多少（℃）` : ""}</span></h3>
              {selExp?.importance?.slice(0, 12).map((r) => { const mx = Math.max(1e-6, ...selExp.importance.map((x) => x.inc)); return (
                <div className="rule" key={r.col}><span style={{ width: 240 }}>{r.col}</span><div className="bar" style={{ flex: 1 }}><b style={{ width: `${Math.max(0, r.inc / mx) * 100}%`, background: r.inc < 0.02 ? "var(--warn)" : "var(--teal)" }} /></div><span className="note" style={{ width: 52, textAlign: "right" }}>{r.inc.toFixed(2)}</span></div>); })}
              {selExp && selExp.importance.length > 12 && <p className="note" style={{ margin: "6px 0 0" }}>另有 {selExp.importance.length - 12} 个输入重要性较低，未列出</p>}
              <p className="note" style={{ margin: "8px 0 0" }}>重要性很低或不符合工艺常识的输入，可回到"加工输入"调整。每个实验单独计算。</p></div>
            <div className="s5"><h3 className="vh">结论 <span className="sub">按规则自动生成</span></h3><div className="note" style={{ fontSize: 14, lineHeight: 1.8, color: "var(--ink)" }}>{cur.conclusion.map((x, i) => <div key={i}>{x}</div>)}</div>
              {selExp && <p className="note">参数：{Object.entries(selExp.params || {}).map(([k, v]) => `${k === "all" ? "" : k + "："}${typeof v === "object" ? Object.entries(v as any).map(([a, b]) => `${a}=${b}`).join(", ") : v}`).join("；")}</p>}
              <div className="row" style={{ marginTop: 12 }}><button className="pri" disabled={!selExp || live || stale} onClick={send}>送去检验效果</button>{selExp && <span className="note">送检 {selExp.code}</span>}</div></div></div>}
        </>}
        <details className="lbhist"><summary>历史训练记录（{Math.max(0, B.length - 1)} 批）</summary>
          {B.slice(1).map((b) => (<details className="lbb" key={b.id}><summary><b>{b.code}</b><span className="note">{b.time} · {b.ds} · {b.note} · {b.exps.length} 个实验</span></summary>
            <ExpTable exps={b.exps} /><div className="note" style={{ marginTop: 6 }}>{b.conclusion.join(" ")}</div></details>))}</details></div>
    </div>
  );
}

function ManInput({ v, onCommit }: { v: number; onCommit: (v: number) => void }) {
  const [s, setS] = useState(String(v));
  useEffect(() => setS(String(v)), [v]);
  return <input type="text" inputMode="decimal" value={s} onChange={(e) => setS(e.target.value)} onBlur={() => { const n = parseFloat(s); if (!isNaN(n) && n !== v) onCommit(n); else setS(String(v)); }} />;
}

const MODES: Record<string, string> = { uni: "统一模型", per: "分工况多模型", main: "只训练主工况" };
const ALGS: Record<string, string> = { base: "基线", lin: "线性回归", rf: "随机森林", xgb: "XGBoost", lstm: "LSTM" };

function ExpTable({ exps, sel, onSel, pick }: { exps: Exp[]; sel?: number; onSel?: (id: number) => void; pick?: boolean }) {
  const done = exps.filter((e) => e.status === "已完成" && e.metrics?.mae != null && e.algo !== "base");
  const best = done.sort((a, b) => a.metrics.mae - b.metrics.mae)[0];
  return (
    <div className="scroll"><table className="stack"><thead><tr><th></th><th>实验</th><th>建模方式</th><th>算法</th><th className="r">总体偏差 ℃</th><th className="r">正常稳定</th><th className="r">高负荷</th><th className="r">最大偏差</th><th className="r">比基线</th><th className="r">区间覆盖</th><th>状态</th></tr></thead>
      <tbody>{exps.map((e) => {
        const live = e.status === "排队中" || e.status === "训练中", m = e.metrics || {}, ok = e.status === "已完成";
        const by = m.by_regime || {};
        return (<tr key={e.id} className={(pick && ok && e.algo !== "base" ? "click" : "") + (sel === e.id ? " sel" : "")} onClick={() => pick && ok && e.algo !== "base" && onSel?.(e.id)}>
          <td>{pick && ok && e.algo !== "base" && <input type="radio" readOnly checked={sel === e.id} aria-label="选择" />}</td>
          <td><b>{e.code}</b>{best && e.id === best.id && <span className="tag t-ok" style={{ marginLeft: 4 }}>最好</span>}</td><td>{MODES[e.mode]}</td><td>{ALGS[e.algo]}</td>
          {live ? <td colSpan={6}>{e.status === "训练中" ? <div className="row" style={{ flexWrap: "nowrap" }}><div className="bar" style={{ flex: 1 }}><b style={{ width: `${e.progress}%` }} /></div><span className="note">{e.progress}% {e.stage}</span></div> : <span className="note">等待中</span>}</td>
            : e.status === "失败" ? <td colSpan={6} className="note err">{e.error.split("\n")[0]}</td>
              : <><td className="r"><b>{f1(m.mae)}</b></td><td className="r">{f1(by.norm?.mae)}</td><td className="r">{by.hi ? (by.hi.mae == null ? "不输出" : f1(by.hi.mae)) : "—"}</td><td className="r">{f1(m.max)}</td>
                <td className="r">{e.algo === "base" ? "—" : m.improve == null ? "—" : `${m.improve > 0 ? "−" : "+"}${Math.abs(m.improve).toFixed(0)}%`}</td><td className="r">{m.coverage == null ? "—" : m.coverage.toFixed(0) + "%"}</td></>}
          <td><span className={"tag " + (e.status === "训练中" ? "t-teal" : e.status === "失败" ? "t-bad" : e.label.startsWith("已发布") ? "t-ok" : e.label === "待检验" ? "t-warn" : "t-mute")}>{e.label || e.status}</span></td></tr>);
      })}</tbody></table></div>
  );
}
