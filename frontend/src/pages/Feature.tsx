import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { EChart } from "../components/EChart";
import { cssVar, Modal, Spinner, useToast } from "../ui";

type PSpec = { cur: boolean; lag: { on: boolean; min: number }; mean: { on: boolean; min: number }; std: { on: boolean; min: number }; rate: { on: boolean; min: number } };
type Combo = { name: string; kind: "formula" | "op" | "script"; expr?: string; zero?: string; op?: string; event?: string; param?: string; tau?: number; unit?: string; on: boolean; src?: string; st?: string; window?: number; code?: string };
type FV = {
  params: { tag: string; name: string; unit: string; spec: PSpec; lag: { v: number; src: string; detail: string } | null; ref: { v: number; src: string } | null }[];
  combos: Combo[]; columns: { col: string; source: string; desc: string; kind: string; excluded?: boolean }[]; tag_names: Record<string, string>;
  ops: Record<string, string>; events: Record<string, string>; code: string | null; dirty: boolean; next: string;
  proxies: { tag: string; name: string; tc: boolean }[]; target_continuous: boolean;
};
type Lag = { r: (number | null)[]; peak: number | null; r_peak: number | null; n: number; sign?: string; included: string[]; weak: boolean };
type Ref = { id: string; type: string; name: string; map: Record<string, { dead_time: number; tau: number }>; imported: boolean };

const KINDS: [keyof PSpec, string][] = [["lag", "前的值"], ["mean", "平均"], ["std", "波动"], ["rate", "变化率"]];
const KIND_TAG: Record<string, [string, string]> = { formula: ["公式", "t-mute"], op: ["算子", "t-teal"], script: ["脚本", "t-acc"] };
const eqLag = (d: number, t: number) => Math.round(d + t / 2);

export default function FeaturePage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const fq = useQuery({ queryKey: ["features", sid], queryFn: () => api.get<FV>(`/api/scenarios/${sid}/features`) });
  const [dlg, setDlg] = useState<{ idx: number | null } | null>(null);
  const [help, setHelp] = useState(false);
  const V = fq.data;
  const set = (d: FV) => { qc.setQueryData(["features", sid], d); qc.invalidateQueries({ queryKey: ["overview", sid] }); };
  const call = async (p: Promise<FV>, ok?: string) => { try { const d = await p; set(d); if (ok) toast(ok); return d; } catch (e) { toast((e as Error).message); } };
  if (!V) return <Spinner text="加载中…" />;
  const ncol = V.columns.filter((c) => !c.excluded).length;
  const putParam = (tag: string, body: object) => call(api.put<FV>(`/api/scenarios/${sid}/features/params/${tag}`, body));
  const saveCombos = (combos: Combo[], ok?: string) => call(api.put<FV>(`/api/scenarios/${sid}/features/combos`, { combos }), ok);

  return (
    <div className="grid">
      <div className="c s12" style={{ padding: "10px 16px" }}><div className="row">
        <span className="note">输入量配置</span><b>{V.code || "尚未保存"}</b>{V.dirty ? <span className="tag t-warn">有未保存的改动</span> : <span className="tag t-ok">已保存</span>}
        <span className="note">加工方式、采用的滞后及来源、组合量一起保存为一个版本；训练和上线调用同一份计算代码</span>
        <button className="pri" style={{ marginLeft: "auto" }} disabled={!V.dirty} onClick={async () => { const d = await call(api.post<FV & { code: string }>(`/api/scenarios/${sid}/features/save`)); if (d) toast(`已保存输入量配置 ${(d as any).code}`); qc.invalidateQueries({ queryKey: ["projects"] }); }}>{V.dirty ? `保存为 ${V.next}` : "已保存"}</button></div></div>

      <div className="c s12"><h2>参数加工方式 <span className="sub">把"选数据"里的每个参数加工成模型直接用的输入；只往回算，不用未来数据</span>
        <span className="row" style={{ marginLeft: "auto", gap: 8 }}><span className="tag t-teal">共 {ncol} 个输入</span><button onClick={() => setHelp(true)}>配置说明</button></span></h2>
        <div className="scroll"><table className="stack fet"><thead><tr><th>参数</th><th>当前值</th><th>过去某时刻的值</th><th>一段时间平均</th><th>一段时间波动</th><th>变化快慢</th></tr></thead>
          <tbody>{V.params.length ? V.params.map((p) => (
            <tr key={p.tag}><td><b>{p.name}</b><div className="note">{p.tag}</div></td>
              <td><label className="fec"><input type="checkbox" checked={p.spec.cur} onChange={(e) => putParam(p.tag, { cur: e.target.checked })} /> 用</label></td>
              {KINDS.map(([k]) => {
                const f = p.spec[k] as { on: boolean; min: number };
                return (<td key={k}><Cell f={f} onToggle={(on) => putParam(p.tag, { [k]: { on } })} onMin={(min) => putParam(p.tag, { [k]: { min } })} />
                  {k === "lag" && (p.lag ? <div className="note">来源：{p.lag.src}</div> : f.on ? <div className="note" style={{ color: "var(--warn)" }}>{p.ref ? `参考 ${p.ref.v} min（${p.ref.src}），` : ""}来源未确认</div> : p.ref ? <div className="note">参考 {p.ref.v} min，来源未确认</div> : null)}</td>);
              })}</tr>)) : <tr><td colSpan={6} className="note" style={{ textAlign: "center", padding: 18 }}>请先在"选数据"中选择参数</td></tr>}</tbody></table></div>
        <p className="note" style={{ margin: "8px 0 0" }}>勾选后填写时间（分钟）；滞后时间可参考下方时滞分析。变化率 = (当前值 − N 分钟前的值) ÷ N × 60，即每小时变化多少。</p></div>

      <LagCard sid={sid} V={V} call={call} />

      <div className="c s12"><h2>组合量与累计量 <span className="sub">单个测点里看不到的信息；可用公式、内置算子或脚本计算</span><button className="pri" style={{ marginLeft: "auto" }} onClick={() => setDlg({ idx: null })}>添加</button></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>启用</th><th>名称</th><th>方式</th><th>定义</th><th></th><th></th></tr></thead>
          <tbody>{V.combos.map((c, i) => (
            <tr key={i}><td><input type="checkbox" aria-label="启用" checked={c.on} onChange={(e) => saveCombos(V.combos.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)))} /></td>
              <td><b>{c.name}</b>{c.unit && <div className="note">{c.unit}</div>}</td><td><span className={"tag " + KIND_TAG[c.kind][1]}>{KIND_TAG[c.kind][0]}</span></td>
              <td className="note">{V.columns.find((x) => x.col === c.name)?.desc || describe(c, V)}</td>
              <td>{c.src === "模板" && <span className="tag t-mute">来自模板</span>}{c.kind === "script" && <span className="tag t-warn">{c.st || "待审核"}</span>}</td>
              <td><div className="row" style={{ flexWrap: "nowrap" }}><button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => setDlg({ idx: i })}>编辑</button>
                <button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => saveCombos(V.combos.filter((_, j) => j !== i), "已删除")}>删除</button></div></td></tr>))}</tbody></table></div>
        <p className="note" style={{ margin: "8px 0 0" }}>先查一下 DCS 或实时库里是否已有现成的计算位号（如稀释比），有的话直接在"选数据"里选，不必重复计算。</p></div>

      <div className="c s12"><h2>加工结果 <span className="sub">训练表中将出现的输入列</span></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>#</th><th>输入列</th><th>来源</th><th>计算方式</th></tr></thead>
          <tbody>{V.columns.map((c, i) => <tr key={i} style={c.excluded ? { opacity: 0.55 } : undefined}><td className="note">{i + 1}</td><td><b>{c.col}</b></td><td>{V.tag_names[c.source] ? `${V.tag_names[c.source]}（${c.source}）` : c.source}</td>
            <td className="note">{c.desc}{c.excluded && "；不进入训练表"}</td></tr>)}</tbody></table></div></div>

      {dlg && <ComboDialog sid={sid} V={V} idx={dlg.idx} onClose={() => setDlg(null)} onSave={async (c) => {
        const list = dlg.idx == null ? [...V.combos, c] : V.combos.map((x, j) => (j === dlg.idx ? c : x));
        const d = await saveCombos(list, `已保存"${c.name}"${c.kind === "script" ? "，审核后可用于发布（Demo 不执行脚本）" : ""}`);
        if (d) setDlg(null);
      }} />}
      <HelpModal open={help} onClose={() => setHelp(false)} onApply={async () => { const d = await call(api.post<FV & { n: number }>(`/api/scenarios/${sid}/features/example`)); if (d) { toast(`已按示例填入 ${(d as any).n} 个参数`); setHelp(false); } }} />
    </div>
  );
}

function describe(c: Combo, V: FV) {
  if (c.kind === "formula") return (c.expr || "").replace(/[{}]/g, "");
  if (c.kind === "op") return `${V.ops[c.op!]}${c.op !== "ema" ? "：" + V.events[c.event || "cycle"] : ""}${c.param ? " · " + V.tag_names[c.param] : ""}`;
  return "Python 脚本";
}

function Cell({ f, onToggle, onMin }: { f: { on: boolean; min: number }; onToggle: (on: boolean) => void; onMin: (m: number) => void }) {
  const [v, setV] = useState(String(f.min));
  useEffect(() => setV(String(f.min)), [f.min]);
  const commit = () => { const n = parseInt(v); if (n > 0 && n !== f.min) onMin(n); else setV(String(f.min)); };
  return (<label className="fec"><input type="checkbox" checked={f.on} onChange={(e) => onToggle(e.target.checked)} />
    <input type="text" className="fev" inputMode="numeric" disabled={!f.on} value={v} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} aria-label="分钟" /><span className="note">min</span></label>);
}

/* ---------------- 时滞分析 ---------------- */
function LagCard({ sid, V, call }: { sid: number; V: FV; call: (p: Promise<FV>, ok?: string) => Promise<FV | undefined> }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [tag, setTag] = useState(V.params.find((p) => p.name === "燃料气流量")?.tag || V.params[0]?.tag || "");
  const tc = V.proxies.find((p) => p.tc);
  const [mode, setMode] = useState<"tgt" | "proxy" | "ext" | "man">(V.target_continuous ? "tgt" : "proxy");
  const nonTc = V.proxies.filter((p) => !p.tc);
  const [proxy, setProxy] = useState(nonTc[0]?.tag || "");
  const [refSel, setRefSel] = useState("");
  const [refType, setRefType] = useState("APC 模型参数");
  const [man, setMan] = useState({ v: "", basis: "操作经验", note: "" });
  const y = mode === "tgt" ? tc?.tag : proxy;
  const lq = useQuery({ queryKey: ["lag", sid, tag, y], queryFn: () => api.get<Lag>(`/api/scenarios/${sid}/lag?tag=${tag}&y=${y}`), enabled: !!tag && !!y && (mode === "tgt" || mode === "proxy") });
  const rq = useQuery({ queryKey: ["lagrefs", sid], queryFn: () => api.get<Ref[]>(`/api/scenarios/${sid}/lag/refs`) });
  const p = V.params.find((x) => x.tag === tag);
  const refs = (rq.data || []).filter((r) => r.map[tag]);
  const curRef = refs.find((r) => r.id === refSel) || refs[0];
  const L = lq.data;
  let cand: { v: number; src: string; d: string; w?: string } | null = null;
  if ((mode === "tgt" || mode === "proxy") && L && L.peak != null) {
    const yn = mode === "tgt" ? "对目标计算" : "代理变量 " + (V.proxies.find((q) => q.tag === proxy)?.name.split("（")[0] || "");
    cand = { v: L.peak, src: yn, d: `峰值 ${L.peak} 分钟，相关系数 ${L.r_peak!.toFixed(2)}（${L.sign}），${L.n.toLocaleString()} 个时间点，工况：${L.included.join("、")}`, w: L.weak ? "峰值不明显，参考价值有限" : undefined };
  }
  if (mode === "ext" && curRef) { const m = curRef.map[tag]; cand = { v: eqLag(m.dead_time, m.tau), src: curRef.type, d: `${curRef.name}：纯滞后 ${m.dead_time} + 时间常数 ${m.tau} 的一半` }; }
  if (mode === "man") { const v = parseInt(man.v); cand = v >= 0 ? { v, src: "手工填写 · " + man.basis, d: man.note.trim() || "未填写说明" } : null; }
  const adopt = () => cand && call(api.post<FV>(`/api/scenarios/${sid}/lag/adopt`, { tag, v: cand.v, src: cand.src, detail: cand.d }), `${p?.name}：滞后 ${cand.v} 分钟（${cand.src}）`);
  const importRef = async (f?: File) => {
    if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("type", refType);
    const r = await fetch(`/api/scenarios/${sid}/lag/import`, { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) { toast(j.detail || "导入失败"); return; }
    toast(`已导入 ${j.name}，${j.n} 个位号`); qc.invalidateQueries({ queryKey: ["lagrefs", sid] });
  };
  const barOpt = useMemo<EChartsOption | null>(() => {
    if (!L) return null;
    const mute = cssVar("--mute"), teal = cssVar("--teal");
    return { animation: false, grid: { left: 44, right: 10, top: 12, bottom: 26 }, tooltip: { trigger: "axis", valueFormatter: (v: any) => (v == null ? "—" : (+v).toFixed(3)) },
      xAxis: { type: "category", data: L.r.map((_, i) => i + " min"), axisLabel: { color: mute, interval: 4 } },
      yAxis: { type: "value", name: "相关系数", nameTextStyle: { color: mute }, axisLabel: { color: mute } },
      series: [{ type: "bar", data: L.r.map((v, i) => ({ value: v, itemStyle: { color: i === L.peak ? "#d9480f" : teal, opacity: i === L.peak ? 1 : 0.55 } })) }] };
  }, [L]);
  const stepOpt = useMemo<EChartsOption | null>(() => {
    if (mode !== "ext" || !curRef) return null;
    const { dead_time: d, tau } = curRef.map[tag], t0 = 5, e = eqLag(d, tau), mute = cssVar("--mute");
    const pts: [number, number][] = [], inp: [number, number][] = [[0, 0], [t0, 0], [t0, 1], [40, 1]];
    for (let t = 0; t <= 40; t += 0.25) pts.push([t, t < t0 + d ? 0 : 1 - Math.exp(-(t - t0 - d) / tau)]);
    return { animation: false, grid: { left: 40, right: 36, top: 14, bottom: 26 }, xAxis: { type: "value", min: 0, max: 40, axisLabel: { color: mute, formatter: "{value} min" } }, yAxis: { type: "value", min: 0, max: 1.1, axisLabel: { color: mute } },
      series: [{ type: "line", data: inp, symbol: "none", lineStyle: { color: mute, type: "dashed" } }, { type: "line", data: pts, symbol: "none", lineStyle: { color: "#d9480f", width: 2 },
        markLine: { symbol: "none", label: { formatter: `等效滞后 ${e} min`, color: "#d9480f" }, lineStyle: { color: "#d9480f", type: "dashed" }, data: [{ xAxis: t0 + e }] } }] };
  }, [mode, curRef, tag]);
  const curTxt = p?.lag ? `当前：${p.lag.v} 分钟 · ${p.lag.src}` : p?.spec.lag.on ? `当前：${p.spec.lag.min} 分钟 · 来源未记录` : "当前未使用滞后";
  return (
    <div className="c s12"><h2>确定滞后时间 <span className="sub">参数变化后，目标要过多久才跟着变</span></h2>
      <div className={"lagHint " + (V.target_continuous ? "ok" : "warn")}>{V.target_continuous
        ? <><b>目标有连续测量</b>：出口段 1# 管装有热电偶（每分钟一个值），可以直接对目标计算；其他管位只有稀疏的红外测温，这里把 1# 管当作目标的代表。</>
        : <><b>目标只有稀疏校核值</b>（红外测温每班一次），无法直接对目标做时滞分析。请用代理变量、导入外部数据或手工填写；加工方式也建议多用"一段时间平均"，不必追求精确滞后。</>}</div>
      <div className="row" style={{ margin: "10px 0" }}><span className="note">参数</span><select aria-label="参数" value={tag} onChange={(e) => setTag(e.target.value)}>{V.params.map((x) => <option key={x.tag} value={x.tag}>{x.name}</option>)}</select><span className="note">{curTxt}</span></div>
      <div className="seg" role="group" aria-label="确定方式">{([["tgt", "对目标计算"], ["proxy", "对代理变量计算"], ["ext", "导入外部数据"], ["man", "手工填写"]] as const).map(([k, n]) => (
        <button key={k} className={mode === k ? "on" : ""} disabled={k === "tgt" && !V.target_continuous} onClick={() => setMode(k)}>{n}</button>))}</div>
      <div style={{ marginTop: 12 }}>
        {mode === "tgt" && <><p className="note" style={{ margin: "0 0 6px" }}>{p?.name} → 炉管壁温（{tc?.tag} 1# 管热电偶连续数据，按"分工况"纳入的工况、所选时段计算；双方先减去 60 分钟滑动均值，去掉结焦等慢变化）</p>
          {lq.isFetching ? <Spinner /> : barOpt && <EChart option={barOpt} height={200} />}</>}
        {mode === "proxy" && <><div className="row" style={{ marginBottom: 6 }}><span className="note">{p?.name} →</span>
          <select aria-label="代理变量" value={proxy} onChange={(e) => setProxy(e.target.value)}>{V.proxies.map((q) => <option key={q.tag} value={q.tag}>{q.name}</option>)}</select>
          <span className="note">用一个连续测量、与目标同步变化的变量代替目标</span></div>
          {lq.isFetching ? <Spinner /> : barOpt && <EChart option={barOpt} height={200} />}</>}
        {mode === "ext" && <><div className="row" style={{ marginBottom: 8 }}>
          <select aria-label="数据类型" value={refType} onChange={(e) => setRefType(e.target.value)}><option>APC 模型参数</option><option>阶跃测试记录</option><option>动态仿真阶跃响应</option></select>
          <label className="tag t-mute" style={{ padding: "8px 12px", cursor: "pointer", borderRadius: 7, fontSize: 14 }}>导入文件（JSON）<input type="file" accept=".json" style={{ display: "none" }} onChange={(e) => { importRef(e.target.files?.[0]); e.target.value = ""; }} /></label>
          <span className="note">格式：{'{"位号": {"dead_time": 分钟, "tau": 分钟}}'}；稳态裂解模拟软件算不出时滞，需动态模型或实测</span></div>
          {refs.length ? <><table><thead><tr><th></th><th>来源</th><th className="r">纯滞后</th><th className="r">时间常数</th><th className="r">等效滞后</th></tr></thead>
            <tbody>{refs.map((r) => (<tr key={r.id} className={"click" + (r.id === curRef?.id ? " sel" : "")} onClick={() => setRefSel(r.id)}>
              <td><input type="radio" readOnly checked={r.id === curRef?.id} aria-label="选择" /></td><td><b>{r.type}</b><div className="note">{r.name}</div></td>
              <td className="r">{r.map[tag].dead_time} min</td><td className="r">{r.map[tag].tau} min</td><td className="r"><b>{eqLag(r.map[tag].dead_time, r.map[tag].tau)} min</b></td></tr>))}</tbody></table>
            <p className="note" style={{ margin: "8px 0 4px" }}>阶跃响应：{p?.name}在第 5 分钟阶跃，橙线为目标的响应（等效滞后 = 纯滞后 + 时间常数的一半）</p>{stepOpt && <EChart option={stepOpt} height={180} />}</>
            : <p className="note" style={{ padding: 14, textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8 }}>已导入的外部数据中没有"{p?.name}"的记录，可导入 APC 模型参数、阶跃测试记录或动态仿真结果</p>}</>}
        {mode === "man" && <><div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}>
          <span className="lab">滞后</span><div className="row"><input type="text" inputMode="numeric" placeholder="分钟" style={{ width: 100 }} value={man.v} onChange={(e) => setMan({ ...man, v: e.target.value })} /><span className="note">分钟</span></div>
          <span className="lab">依据</span><select value={man.basis} onChange={(e) => setMan({ ...man, basis: e.target.value })}><option>操作经验</option><option>文献参考</option><option>厂商资料</option></select>
          <span className="lab">说明</span><input type="text" placeholder="例如：班长经验，调燃料气后 10 分钟左右壁温有反应" value={man.note} onChange={(e) => setMan({ ...man, note: e.target.value })} /></div>
          <p className="note" style={{ margin: "8px 0 0" }}>文献和厂商资料一般只给量级，适合用来判断其他方式的结果是否合理。</p></>}
      </div>
      <div className="lagRes">{cand ? <div className="row"><span>结果 <b style={{ fontSize: 20 }}>{cand.v} 分钟</b></span><span className="tag t-mute">{cand.src}</span><span className="note">{cand.d}</span>
        {cand.w && <span className="note" style={{ color: "var(--warn)" }}>{cand.w}</span>}{cand.v > 25 && <span className="note err">超过 25 分钟，请工艺人员确认是否合理</span>}
        <button className="pri" style={{ marginLeft: "auto" }} onClick={adopt}>采用为该参数的滞后</button></div>
        : <p className="note" style={{ margin: 0 }}>{mode === "man" ? "填写滞后分钟数后可以采用" : lq.isFetching ? "计算中…" : "暂无可用结果，可换一种方式确定"}</p>}</div>
    </div>
  );
}

/* ---------------- 组合量弹窗 ---------------- */
const PYT = `def compute(window):
    """window：过去 N 分钟的数据，按参数名取列；只包含当前及以前的数据"""
    fuel = window["燃料气流量"]
    feed = window["原料流量"]
    if feed.mean() < 1:
        return None          # 停料时不计算
    return fuel.mean() / feed.mean()`;

type Prev = { t: string[]; v: (number | null)[]; events?: [string, string, string][]; msg: string; valid_pct?: number; script?: boolean };

function ComboDialog({ sid, V, idx, onClose, onSave }: { sid: number; V: FV; idx: number | null; onClose: () => void; onSave: (c: Combo) => void }) {
  const init: Combo = idx != null ? JSON.parse(JSON.stringify(V.combos[idx])) : { name: "", kind: "formula", expr: "", zero: "invalid", op: "since", event: "cycle", tau: 3, unit: "", on: true, window: 60, code: PYT };
  const [c, setC] = useState<Combo>(init);
  const [res, setRes] = useState<Prev | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const exprRef = useState<{ el: HTMLInputElement | null }>({ el: null })[0];
  const names = V.params.map((p) => p.name);
  const up = (p: Partial<Combo>) => { setC({ ...c, ...p }); setRes(null); };
  const ins = (s: string) => { const el = exprRef.el; const e = c.expr || ""; const a = el?.selectionStart ?? e.length, b = el?.selectionEnd ?? a; up({ expr: e.slice(0, a) + s + e.slice(b) }); setTimeout(() => el?.focus(), 0); };
  const run = async () => {
    setErr(""); setBusy(true);
    try { setRes(await api.post<Prev>(`/api/scenarios/${sid}/features/preview`, { combo: c })); } catch (e) { setErr((e as Error).message); setRes(null); } finally { setBusy(false); }
  };
  useEffect(() => { if (idx != null && c.kind !== "script") run(); }, []);
  const save = () => {
    if (!c.name.trim()) { setErr("请填写名称"); return; }
    if (!res) { setErr("请先试算，确认结果合理再保存"); return; }
    onSave({ ...c, name: c.name.trim(), src: idx != null ? c.src : "" });
  };
  const opt = useMemo<EChartsOption | null>(() => {
    if (!res || res.script || !res.t.length) return null;
    const mute = cssVar("--mute");
    const areas = (res.events || []).filter((e) => e[2] !== "sw").map(([a, b, w]) => [{ xAxis: a, itemStyle: { color: w === "burn" ? "#c92a2a" : mute, opacity: w === "burn" ? 0.16 : 0.2 } }, { xAxis: b }]);
    const lines = (res.events || []).filter((e) => e[2] === "sw").map(([a]) => ({ xAxis: a }));
    return { animation: false, grid: { left: 56, right: 12, top: 10, bottom: 26 }, tooltip: { trigger: "axis", valueFormatter: (v: any) => (v == null ? "—" : (+v).toFixed(3)) },
      xAxis: { type: "time", axisLabel: { color: mute } }, yAxis: { type: "value", scale: true, axisLabel: { color: mute } },
      series: [{ type: "line", data: res.t.map((t, i) => [t, res.v[i]]), symbol: "none", lineStyle: { color: "#d9480f", width: 1.6 },
        markArea: { silent: true, data: areas as any }, markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: "#7048e8", type: "dashed" }, data: lines } }] };
  }, [res]);
  return (
    <Modal open onClose={onClose} wide>
      <h2>{idx != null ? "编辑" : "添加"}组合量 / 累计量<button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr) 60px 120px", marginBottom: 12 }}><span className="lab">名称</span><input type="text" placeholder="例如：稀释比" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} />
        <span className="lab">单位</span><input type="text" placeholder="可空" value={c.unit || ""} onChange={(e) => setC({ ...c, unit: e.target.value })} /></div>
      <div className="tabs">{(["formula", "op", "script"] as const).map((k) => <button key={k} className={"tab" + (c.kind === k ? " on" : "")} onClick={() => up({ kind: k })}>{k === "formula" ? "公式（四则运算）" : k === "op" ? "内置算子（累计、事件、平滑）" : "脚本（Python）"}</button>)}</div>
      {c.kind === "formula" && <div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}><span className="lab">表达式</span><div>
        <input type="text" ref={(el) => { exprRef.el = el; }} placeholder="点下面的参数和运算符拼出公式" style={{ width: "100%" }} value={c.expr || ""} onChange={(e) => up({ expr: e.target.value })} />
        <div className="chips" style={{ margin: "8px 0 0" }}>{names.map((n) => <button key={n} className="chip" onClick={() => ins(`{${n}}`)}>{n}</button>)}
          {[["＋", " + "], ["−", " − "], ["×", " × "], ["÷", " ÷ "], ["(", "("], [")", ")"]].map(([l, s]) => <button key={l} className="chip" onClick={() => ins(s)}>{l}</button>)}
          {["abs", "log", "sqrt"].map((f) => <button key={f} className="chip" onClick={() => ins(f + "(")}>{f}( )</button>)}</div></div>
        <span className="lab">除数为零</span><select value={c.zero} onChange={(e) => up({ zero: e.target.value })}><option value="invalid">结果标为无效（推荐）</option><option value="hold">沿用上一个有效值</option></select></div>}
      {c.kind === "op" && <><div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}>
        <span className="lab">算子</span><select value={c.op} onChange={(e) => up({ op: e.target.value })}>{Object.entries(V.ops).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select>
        {c.op !== "ema" && <><span className="lab">事件</span><select value={c.event} onChange={(e) => up({ event: e.target.value })}>{Object.entries(V.events).map(([k, n]) => <option key={k} value={k}>{n}（来自"分工况"）</option>)}</select></>}
        {(c.op === "cum" || c.op === "ema") && <><span className="lab">参数</span><select value={c.param || ""} onChange={(e) => up({ param: e.target.value })}><option value="">请选择</option>{V.params.map((p) => <option key={p.tag} value={p.tag}>{p.name}</option>)}</select></>}
        {c.op === "ema" && <><span className="lab">时间常数</span><div className="row"><input type="text" inputMode="decimal" style={{ width: 80 }} value={c.tau ?? 3} onChange={(e) => up({ tau: parseFloat(e.target.value) || 3 })} /><span className="note">天</span></div></>}</div>
        <p className="note" style={{ margin: "8px 0 0" }}>事件按"分工况"的规则实时判定（上线时同样如此）。{c.op === "since" ? "单位：天；烧焦/停炉期间为空。" : c.op === "ago" ? "单位：小时。" : c.op === "cum" ? "累计量 = 参数（每小时的量）× 小时数，例如原料 t/h → 累计 t。" : "逐分钟递推，只用历史值。"}第一个周期的起点可在"分工况 → 运行周期"中按运行记录填写。</p></>}
      {c.kind === "script" && <><div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}>
        <span className="lab">历史窗口</span><div className="row"><input type="text" inputMode="numeric" style={{ width: 80 }} value={c.window ?? 60} onChange={(e) => up({ window: parseInt(e.target.value) || 60 })} /><span className="note">分钟，平台只把这段过去的数据传给脚本</span></div>
        <span className="lab">脚本</span><textarea spellCheck={false} style={{ fontFamily: "monospace", fontSize: 13, minHeight: 170, whiteSpace: "pre" }} value={c.code ?? PYT} onChange={(e) => up({ code: e.target.value })} /></div>
        <p className="note" style={{ margin: "8px 0 0" }}>脚本随模型一起发布，保存后需审核才能用于发布的模型。<b>Demo 版本只保存脚本文本，不执行任何用户代码，也不进入训练表。</b></p></>}
      <div className="row" style={{ marginTop: 12 }}><button disabled={busy} onClick={run}>{c.kind === "script" ? "在历史数据上试算" : busy ? "计算中…" : "试算预览"}</button><span className="note">{res?.msg}{res && res.valid_pct != null ? ` · 所选时段有效 ${res.valid_pct}%` : ""}</span></div>
      {opt && <><EChart option={opt} height={200} /><div className="legend"><span><i style={{ background: "#d9480f" }} />计算结果</span><span><i style={{ background: "#c92a2a", opacity: 0.3, height: 10 }} />烧焦</span><span><i style={{ background: "var(--mute)", opacity: 0.35, height: 10 }} />停炉检修</span><span><i style={{ background: "#7048e8", height: 2 }} />原料切换</span></div></>}
      <div className="row" style={{ marginTop: 12 }}><span className="note err">{err}</span><button style={{ marginLeft: "auto" }} onClick={onClose}>取消</button><button className="pri" onClick={save}>保存</button></div>
    </Modal>
  );
}

/* ---------------- 配置说明 ---------------- */
const FEX: [string, string, string[], string][] = [
  ["FI-1021", "燃料气流量", ["当前值", "8 分钟前的值"], "燃料气阀门开大后，要经过燃烧、炉膛辐射、管壁传热，壁温才升上来，需要几分钟。时滞分析峰值在 8 分钟，和经验一致，所以取 8 分钟前的值；当前值也保留，反映正在发生的调整。"],
  ["FI-1001", "原料流量", ["当前值", "12 分钟前的值"], "进料变化改变管内吸热，物料从入口流到出口段有停留时间，影响到出口段壁温要更久一些，取 12 分钟前。"],
  ["TI-1052", "炉膛温度", ["当前值", "10 分钟平均"], "热电偶读数毛刺多，单个瞬时值容易误导；炉膛热惯性大，10 分钟平均更能代表真实热负荷。它和壁温几乎同步，所以不用滞后。"],
  ["TI-1041", "出口温度", ["当前值", "10 分钟平均", "10 分钟波动"], "和壁温同步，直接用当前值和均值。另外勾\"波动\"：出口温度由控制器在调，波动大说明正处在调节过程中，这时壁温和出口温度的关系与平稳时不一样，模型需要知道\"现在稳不稳\"。"],
  ["PDI-1063", "炉管压降", ["当前值", "30 分钟变化快慢"], "压降缓慢上涨反映管内结焦，结焦会让壁温升高，所以关心它\"在涨还是在稳\"。注意窗口长短：30 分钟只看得到短时变化，结焦是按天累积的，长期趋势交给下面的\"本周期运行天数\"。"],
  ["AI-1071", "烟气氧含量", ["当前值", "5 分钟前的值", "10 分钟平均"], "分析仪本身有分析滞后，读数噪声也大，所以既取 5 分钟前的值，又取 10 分钟平均。"],
];
const FTYPE = [["温度（热电偶）", "多半和目标同步，噪声较多", "当前值 + 一段时间平均（5–15 分钟）"], ["流量、阀位（操作量）", "改变后目标要过一会儿才反应", "当前值 + 过去某时刻的值（按时滞）"], ["在线分析仪（组分、氧含量）", "有分析滞后，噪声大", "过去某时刻的值 + 一段时间平均"], ["压力、压差", "关心趋势多于瞬时值", "当前值 + 变化快慢"], ["缓慢累积的过程（结焦、结垢、催化剂失活）", "单个测点看不出来", "用累计量，如运行天数、累计处理量"]];
const Q4 = [["它变了之后，目标是马上变，还是过一会儿才变？", "过一会儿 → 勾\"过去某时刻的值\"，时间填反应需要的分钟数（参考时滞分析，再按经验确认）"], ["它的读数跳不跳、噪声大不大？", "跳 → 勾\"一段时间平均\"，一般 5–15 分钟，窗口越长越平滑，但反应越慢"], ["它稳不稳，本身会不会影响目标？", "会 → 勾\"一段时间波动\""], ["关心的是它在涨还是在降，而不只是数值大小？", "是 → 勾\"变化快慢\"，窗口按趋势的时间尺度选：短时调整 10–30 分钟，长期累积用小时或天"]];

function HelpModal({ open, onClose, onApply }: { open: boolean; onClose: () => void; onApply: () => void }) {
  return (
    <Modal open={open} onClose={onClose} wide>
      <h2>配置说明 <span className="sub">示例场景：裂解炉 · 炉管壁温软测量</span><button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <p style={{ margin: "0 0 12px" }}>加工方式要回答的是：<b>这个参数对目标的影响是什么样的</b>。对每个参数，依次问自己四个问题：</p>
      <div className="q4">{Q4.map((q, i) => <div key={i}><span className="qn">{i + 1}</span><b>{q[0]}</b><div className="note">{q[1]}</div></div>)}</div>
      <p className="note" style={{ margin: "8px 0 0" }}>"当前值"一般都保留；不确定时宁可少勾，输入多了模型反而容易学到偶然规律。</p>
      <h3>示例：每个参数为什么这样选</h3>
      <div className="scroll"><table className="stack"><thead><tr><th style={{ width: 110 }}>参数</th><th style={{ width: 220 }}>勾选</th><th>工艺上的理由</th></tr></thead>
        <tbody>{FEX.map(([t, n, c, why]) => <tr key={t}><td><b>{n}</b><div className="note">{t}</div></td><td>{c.map((x) => <span key={x} className="tag t-teal" style={{ margin: "2px 4px 2px 0" }}>{x}</span>)}</td><td>{why}</td></tr>)}
          <tr><td><b>组合量、累计量</b></td><td><span className="tag t-teal">稀释比</span> <span className="tag t-teal">本周期运行天数</span></td><td>稀释比影响管内结焦速度和传热，真正起作用的是比值。同样的操作条件下，周期末的壁温明显高于周期初，这个差别只能靠"运行天数"告诉模型。</td></tr></tbody></table></div>
      <h3>套用到你的现场：按参数类型找起点</h3>
      <div className="scroll"><table className="stack"><thead><tr><th>参数类型</th><th>通常的特点</th><th>建议起点</th></tr></thead><tbody>{FTYPE.map((r) => <tr key={r[0]}><td><b>{r[0]}</b></td><td>{r[1]}</td><td>{r[2]}</td></tr>)}</tbody></table></div>
      <h3>几个常见问题</h3>
      <div className="note" style={{ fontSize: 14, lineHeight: 1.8 }}>· 时滞分析算出的时间远超工艺常识（比如燃料气 40 分钟），多半是数据或控制回路的干扰，先别采用，请工艺确认。<br />· 每种都勾上不会更准。训练后到"训练模型"看输入重要性，长期接近 0 的可以去掉。<br />· 平均和变化快慢都只往回算，平台不会使用未来数据，训练和上线的计算方式一致。</div>
      <div className="row" style={{ marginTop: 14 }}><span className="note">示例只对"选数据"中已选的参数生效</span><button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button><button className="pri" onClick={onApply}>按示例填入</button></div>
    </Modal>
  );
}
