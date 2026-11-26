import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Furnace, Scenario, Tag, Target } from "../api";
import { EChart } from "../components/EChart";
import ParamPicker from "../components/ParamPicker";
import { Bar, cssVar, fmtN, Spinner, useToast } from "../ui";

type Summary = {
  selected: { tag: string; name: string; device: string; part: string; desc: string; unit: string; period: string; completeness: number }[];
  quality: { tag: string; name: string; completeness: number; usable: number; freeze: number; spike: number; range: number; grade: string }[];
  impact: { lost_pct: number; top: { name: string; pct: number }[]; suggest: { name: string; gain_pct: number } | null };
  calib: {
    sources: { id: string; use: boolean; name: string; kind: string; status: string; from: string | null; to: string | null; available: number; position: string; note: string; training: boolean; import_id: number | null }[];
    total: number; tc: number; ir: number; coverage: { id: string; name: string; n: number }[]; pick: string; warn: boolean;
  };
  gen: { n_tags: number; start: string; end: string; eff_days: number; calib_points: number };
};
type RuleItem = { kind: string; title: string; params: Record<string, any>; desc: string; actions: string[]; src: string | null };
type Rules = { items: { key: string; name: string; unit: string; rules: RuleItem[] }[]; code: string | null; dirty: boolean; next: string };
type DS = { code: string; start: string; end: string; n_tags: number; calib_points: number; rule_code: string; lost_pct: number; created_at: string; used_by: string[] };

export default function DataPage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const scq = useQuery({ queryKey: ["scenario", sid], queryFn: () => api.get<Scenario>(`/api/scenarios/${sid}`) });
  const tagq = useQuery({ queryKey: ["tags", sid], queryFn: () => api.get<Tag[]>(`/api/scenarios/${sid}/tags`) });
  const furq = useQuery({ queryKey: ["furnaces"], queryFn: () => api.get<Furnace[]>("/api/furnaces") });
  const sumq = useQuery({ queryKey: ["summary", sid], queryFn: () => api.get<Summary>(`/api/scenarios/${sid}/data/summary`), placeholderData: (p) => p });
  const [picker, setPicker] = useState(false);

  const refresh = (keys: string[] = ["scenario", "summary", "preview", "rules"]) =>
    Promise.all(keys.map((k) => qc.invalidateQueries({ queryKey: [k, sid] })));
  const putData = async (body: Record<string, unknown>, keys?: string[]) => {
    try {
      await api.put(`/api/scenarios/${sid}/data`, body);
      await refresh(keys);
      return true;
    } catch (e) { toast((e as Error).message); return false; }
  };

  if (!scq.data || !tagq.data || !furq.data) return <Spinner text="加载中…" />;
  const sc = scq.data;
  const d = sc.config.data;
  const tagMap = Object.fromEntries(tagq.data.map((t) => [t.tag, t]));
  const fur = furq.data.find((f) => f.unit === sc.furnace)!;
  const S = sumq.data;
  const busy = sumq.isFetching;

  return (
    <div className="grid">
      <TargetCard sc={sc} onSaved={() => { refresh(["scenario", "summary", "rules", "preview"]); qc.invalidateQueries({ queryKey: ["projects"] }); }} />

      <div className="c s12">
        <h2>已选参数 <span className="sub">{d.tags.length} 个</span><span className="row" style={{ marginLeft: "auto" }}>
          <button onClick={async () => { const rec = tagq.data!.filter((t) => t.recommended && !t.is_target).map((t) => t.tag); if (await putData({ tags: rec })) toast(`已按模板推荐选择 ${rec.length} 个参数`); }}>按模板推荐选择</button>
          <button className="pri" onClick={() => setPicker(true)}>选择参数</button></span></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>设备</th><th>参数</th><th>测点</th><th>单位</th><th className="r">采样周期</th><th style={{ width: "20%" }}>所选时段完整率</th><th></th></tr></thead>
          <tbody className={busy ? "loading" : ""}>{d.tags.length ? d.tags.map((t: string) => {
            const x = tagMap[t], q = S?.selected.find((s) => s.tag === t);
            return (<tr key={t}><td className="note">{x.device.split(" ")[0]}{x.part ? " / " + x.part : ""}</td><td><b>{x.name}</b></td><td>{x.tag}<div className="note">{x.desc}</div></td><td>{x.unit}</td><td className="r">{x.period}</td>
              <td>{q ? <Bar pct={q.completeness} warn={q.completeness < 90} /> : <span className="note">计算中…</span>}</td>
              <td><button style={{ minHeight: 30, padding: "2px 10px" }} aria-label={"移除 " + x.name} onClick={() => putData({ tags: d.tags.filter((y: string) => y !== t) })}>移除</button></td></tr>);
          }) : <tr><td colSpan={7} className="note" style={{ textAlign: "center", padding: 20 }}>点击右上角"选择参数"</td></tr>}</tbody></table></div>
        <TimeRange d={d} fur={fur} putData={putData} />
      </div>

      <Preview sid={sid} sc={sc} tagMap={tagMap} putData={putData} />

      <CalibCard sid={sid} sc={sc} S={S} busy={busy} putData={putData} refresh={refresh} />

      <div className="c s7"><h2>数据质量 <span className="sub">已选参数在所选时段内（扣除排除时段）</span></h2>
        {!S ? <Spinner /> : <div className={busy ? "loading" : ""}>
          <div className="scroll"><table className="stack"><thead><tr><th>参数</th><th>完整率</th><th className="r">冻结段</th><th className="r">跳变</th><th className="r">超范围</th><th className="r">可用率</th><th>评级</th></tr></thead>
            <tbody>{S.quality.map((q) => (
              <tr key={q.tag}><td>{q.name}</td><td><Bar pct={q.completeness} warn={q.completeness < 90} /></td><td className="r">{q.freeze}</td><td className="r">{q.spike}</td><td className="r">{q.range}</td><td className="r">{q.usable.toFixed(1)}%</td>
                <td><span className={"tag " + (q.grade === "A" ? "t-ok" : q.grade === "B" ? "t-teal" : "t-warn")}>{q.grade}</span></td></tr>))}</tbody></table></div>
          <p className="note" style={{ margin: "6px 0 0" }}>冻结段、超范围按"段"计，跳变按点计；可用率为按当前处理规则最终能参与训练的比例。评级：可用率 ≥ 98% 为 A，≥ 93% 为 B，其余为 C。</p>
          {S.quality.length > 0 && <div className="vimp" style={{ marginTop: 12, gridTemplateColumns: "1fr" }}><div className="t">
            按当前规则，所选时段内约 <b style={{ fontSize: 16 }}>{S.impact.lost_pct.toFixed(1)}%</b> 的时间点因为至少一个参数数据无效，样本不参与训练。
            {S.impact.top.length > 0 && <>影响最大的是：{S.impact.top.map((t) => `${t.name} ${t.pct.toFixed(1)}%`).join("、")}。</>}
            {S.impact.suggest && <> 如果"{S.impact.suggest.name}"不是必需的输入，去掉它能多保留约 {S.impact.suggest.gain_pct.toFixed(1)}% 的时间点。</>}
          </div></div>}
        </div>}
      </div>
      <RulesCard sid={sid} sc={sc} refresh={refresh} />

      <GenCard sid={sid} S={S} busy={busy} />

      <ParamPicker open={picker} tags={tagq.data} selected={d.tags} onClose={() => setPicker(false)}
        onOk={async (s) => { setPicker(false); if (await putData({ tags: s })) toast(`已选择 ${s.length} 个参数`); }} />
    </div>
  );
}

/* ---------------- 估算目标 ---------------- */
function TargetCard({ sc, onSaved }: { sc: Scenario; onSaved: () => void }) {
  const [g, setG] = useState<Target>(sc.config.target);
  const [err, setErr] = useState("");
  useEffect(() => setG(sc.config.target), [sc.id]);
  const save = async (n: Target) => {
    setG(n);
    try {
      await api.put(`/api/scenarios/${sc.id}/target`, { ...n, lo: Number(n.lo), hi: Number(n.hi) });
      setErr("");
      onSaved();
    } catch (e) { setErr((e as Error).message); }
  };
  const setF = (k: keyof Target, v: any) => setG({ ...g, [k]: v });
  return (
    <div className="c s12 tgc"><h2>估算目标 <span className="sub">这个场景要估算的量。一个场景只有一个目标，后面所有步骤都围绕它</span></h2>
      <div className="tgf">
        <label><span className="lab">目标名称</span><input type="text" value={g.name} onChange={(e) => setF("name", e.target.value)} onBlur={() => save(g)} /></label>
        <label><span className="lab">类型</span><select value={g.type} onChange={(e) => save({ ...g, type: e.target.value as Target["type"] })}><option value="soft">软测量：估算当前值</option><option value="fc">时序预测：预测未来值</option></select></label>
        {g.type === "fc" && <label><span className="lab">预测时域</span><span className="row">{[5, 15, 30, 60].map((h) => (
          <label key={h} className="row" style={{ gap: 4, cursor: "pointer" }}><input type="checkbox" checked={g.horizons.includes(h)} onChange={(e) => save({ ...g, horizons: e.target.checked ? [...g.horizons, h].sort((a, b) => a - b) : g.horizons.filter((x) => x !== h) })} />{h} 分钟</label>))}</span></label>}
        <label><span className="lab">单位</span><input type="text" style={{ width: 90 }} value={g.unit} onChange={(e) => setF("unit", e.target.value)} onBlur={() => save(g)} /></label>
        <label><span className="lab">合理范围</span><span className="row" style={{ flexWrap: "nowrap" }}>
          <input type="text" inputMode="decimal" style={{ width: 90 }} value={g.lo} onChange={(e) => setF("lo", e.target.value)} onBlur={() => save(g)} /><span>–</span>
          <input type="text" inputMode="decimal" style={{ width: 90 }} value={g.hi} onChange={(e) => setF("hi", e.target.value)} onBlur={() => save(g)} /></span></label>
        <label><span className="lab">校核值取法</span><select value={g.pick} onChange={(e) => save({ ...g, pick: e.target.value as Target["pick"] })}><option value="avg">出口段 1#–8# 管校核值按时刻取平均</option><option value="t1">只用出口段 1# 管</option></select></label>
        <label><span className="lab">输出位号</span><input type="text" style={{ width: 160 }} value={g.output_tag} onChange={(e) => setF("output_tag", e.target.value)} onBlur={() => save(g)} /></label>
      </div>
      <p className="note" style={{ margin: "10px 0 0" }}>模型输出写入本地输出表，位号 <b>{g.output_tag || "—"}</b>，仅用于监视参考，<b>不进入控制回路</b>。{g.type === "fc" ? "每个预测时域分别训练和检验，时域越远一般越不准。" : ""}
        {g.pick === "avg" ? " 按时刻平均：同一次巡检至少测到 4 根出口段管才形成一个校核样本。" : " 只用 1# 管：1# 管热电偶整点值和红外测温的 1# 管记录都作为校核样本。"}</p>
      <p className="note err" style={{ margin: "4px 0 0" }}>{err}</p>
    </div>
  );
}

/* ---------------- 时间范围与排除时段 ---------------- */
function TimeRange({ d, fur, putData }: { d: any; fur: Furnace; putData: (b: Record<string, unknown>, k?: string[]) => Promise<boolean> }) {
  const [xs, setXs] = useState(""), [xe, setXe] = useState(""), [xr, setXr] = useState(""), [err, setErr] = useState("");
  const quick = (m: number) => {
    const end = fur.train_to;
    let start = fur.from;
    if (m) { const t = new Date(end + "T00:00:00Z"); t.setUTCMonth(t.getUTCMonth() - m); t.setUTCDate(t.getUTCDate() + 1); start = t.toISOString().slice(0, 10); }
    putData({ start, end });
  };
  const add = () => {
    if (!xs || !xe) { setErr("请填写起止日期"); return; }
    if (xs > xe) { setErr("开始日期不能晚于结束日期"); return; }
    setErr("");
    putData({ excludes: [...d.excludes, { start: xs, end: xe, reason: xr.trim() || "未注明" }] });
    setXs(""); setXe(""); setXr("");
  };
  return (<>
    <h3 className="vh">时间范围 <span className="sub">可用数据 {fur.from} 至 {fur.to}；{fur.train_to} 之后的数据留给"上线投用"回放</span></h3>
    <div className="row"><input type="date" className="din" value={d.start} onChange={(e) => e.target.value && putData({ start: e.target.value })} /><span>至</span>
      <input type="date" className="din" value={d.end} onChange={(e) => e.target.value && putData({ end: e.target.value })} />
      <button onClick={() => quick(3)}>近 3 个月</button><button onClick={() => quick(6)}>近 6 个月</button><button onClick={() => quick(0)}>全部可用</button></div>
    <h3 className="vh">排除时间段 <span className="sub">停炉、检修、数据中断等不参与训练</span></h3>
    {d.excludes.length ? d.excludes.map((e: any, i: number) => (
      <div className="rule" key={i}><span>{e.start} 至 {e.end}</span><span className="note">{e.reason}</span>
        <button className="x" style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => putData({ excludes: d.excludes.filter((_: any, j: number) => j !== i) })}>删除</button></div>)) : <p className="note">无</p>}
    <div className="row" style={{ marginTop: 6 }}><input type="date" className="din" value={xs} onChange={(e) => setXs(e.target.value)} /><span>至</span><input type="date" className="din" value={xe} onChange={(e) => setXe(e.target.value)} />
      <input type="text" placeholder="原因" style={{ width: 140 }} value={xr} onChange={(e) => setXr(e.target.value)} /><button onClick={add}>添加</button></div>
    <p className="note err" style={{ margin: "4px 0 0" }}>{err}</p>
  </>);
}

/* ---------------- 多参数预览 ---------------- */
type PreviewData = {
  t: string[]; step_minutes: number; start: string; end: string; excludes: { start: string; end: string; reason: string }[];
  series: { tag: string; name: string; unit: string; mean: (number | null)[]; min: (number | null)[]; max: (number | null)[]; bad_spans: [string, string][]; marks: [string, number | null, string][] }[];
  calib?: [string, number, string][]; target?: { name: string; unit: string };
};

function Preview({ sid, sc, tagMap, putData }: { sid: number; sc: Scenario; tagMap: Record<string, Tag>; putData: (b: Record<string, unknown>, k?: string[]) => Promise<boolean> }) {
  const toast = useToast();
  const d = sc.config.data;
  const [flag, setFlag] = useState(true);
  const [ck, setCk] = useState(true);
  const [win, setWin] = useState<[string, string] | null>(null);
  const pv: string[] = (d.preview || []).filter((t: string) => d.tags.includes(t));
  const show: string[] = pv.length ? pv : d.tags.slice(0, 2);
  useEffect(() => setWin(null), [d.start, d.end]);
  const q = useQuery({
    queryKey: ["preview", sid, show.join(","), ck, win?.join("~") || "", d.start, d.end, JSON.stringify(d.excludes)],
    queryFn: () => api.get<PreviewData>(`/api/scenarios/${sid}/preview?tags=${show.join(",")}&buckets=700&calib_on=${ck}` + (win ? `&start=${encodeURIComponent(win[0])}&end=${encodeURIComponent(win[1])}` : "")),
    placeholderData: (p) => p,
  });
  const P = q.data;
  const panels = (P?.series.length || 0) + (ck && P?.calib ? 1 : 0);
  const height = Math.max(180, panels * 100 + 36);
  const option = useMemo<EChartsOption | null>(() => P ? buildPreview(P, flag, ck) : null, [P, flag, ck]);
  const zoomT = useRef<number>();
  const toggle = (t: string) => {
    if (show.includes(t)) putData({ preview: show.filter((x: string) => x !== t) }, ["scenario"]);
    else if (show.length >= 5) toast("最多同时查看 5 个参数");
    else putData({ preview: [...show, t] }, ["scenario"]);
  };
  const shift = (f: number) => {
    if (!P) return;
    const a = Date.parse(P.start.replace(" ", "T")), b = Date.parse(P.end.replace(" ", "T")), span = b - a;
    let na: number, nb: number;
    if (f === 0) { setWin(null); return; }
    if (f === 2) { const c = (a + b) / 2; na = c - span; nb = c + span; } else { na = a + span * 0.5 * f; nb = b + span * 0.5 * f; }
    setWin([fmtT(na), fmtT(nb)]);
  };
  return (
    <div className="c s12"><h2>数据预览 <span className="sub">可同时看多个参数，最多 5 个；滚轮缩放、拖动平移，松开后按新范围重新取数</span>
      <span className="row" style={{ marginLeft: "auto", gap: 14 }}>
        <label className="row note" style={{ gap: 6, cursor: "pointer" }}><input type="checkbox" checked={flag} onChange={(e) => setFlag(e.target.checked)} /> 标出被处理的时段</label>
        <label className="row note" style={{ gap: 6, cursor: "pointer" }}><input type="checkbox" checked={ck} onChange={(e) => setCk(e.target.checked)} /> 显示训练用校核值</label></span></h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <div className="chips" style={{ margin: 0, flex: 1 }}>{d.tags.map((t: string) => <button key={t} className={"chip" + (show.includes(t) ? " on" : "")} onClick={() => toggle(t)}>{tagMap[t]?.name}</button>)}</div>
        <span className="row" style={{ gap: 4 }}><button onClick={() => shift(-1)} aria-label="向前">◀</button><button onClick={() => shift(2)}>缩小</button><button onClick={() => shift(0)}>全部</button><button onClick={() => shift(1)} aria-label="向后">▶</button></span>
      </div>
      {P && <p className="note" style={{ margin: "0 0 4px" }}>显示 {P.start} 至 {P.end} · 每个点代表 {P.step_minutes >= 60 ? (P.step_minutes / 60).toFixed(P.step_minutes % 60 ? 1 : 0) + " 小时" : P.step_minutes + " 分钟"}的平均值{q.isFetching ? " · 加载中…" : ""}</p>}
      <div className={q.isFetching ? "loading" : ""}>{option ? <EChart option={option} height={height} onEvents={{
        datazoom: (_p, chart) => {
          clearTimeout(zoomT.current);
          zoomT.current = window.setTimeout(() => {
            const dz = (chart.getOption() as any).dataZoom?.[0];
            if (dz && dz.startValue != null && (dz.start > 0.5 || dz.end < 99.5)) setWin([fmtT(dz.startValue), fmtT(dz.endValue)]);
          }, 350);
        },
      }} /> : <Spinner />}</div>
      <div className="legend"><span><i style={{ background: "#d9480f" }} />参数均值（处理后）</span><span><i style={{ background: "#d9480f", opacity: 0.2, height: 10 }} />区间内最小–最大</span>
        <span><i style={{ background: "var(--ink)", width: 8, height: 8, borderRadius: "50%" }} />校核值（{sc.config.target.name}）</span><span><i style={{ background: "var(--mute)", opacity: 0.35, height: 10 }} />排除时间段</span>
        <span><i style={{ background: "#c92a2a", opacity: 0.35, height: 10 }} />冻结 / 长时间缺失（不参与训练）</span><span><i style={{ background: "#e67700", width: 8, height: 8, borderRadius: "50%" }} />超范围 / 尖峰（已剔除）</span></div>
    </div>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const fmtT = (ms: number) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function buildPreview(P: PreviewData, flag: boolean, ck: boolean): EChartsOption {
  const ink = cssVar("--ink", "#1a1f24"), mute = cssVar("--mute", "#5f6872"), line = cssVar("--line", "#e1e4e8");
  const panels: { name: string; unit: string }[] = P.series.map((s) => ({ name: s.name, unit: s.unit }));
  const withCk = ck && !!P.calib;
  if (withCk) panels.push({ name: "校核值", unit: P.target?.unit || "" });
  const ph = 80, gap = 20, top = 8;
  const grid: any[] = [], xAxis: any[] = [], yAxis: any[] = [], series: any[] = [];
  const exAreas = P.excludes.map((e) => [{ xAxis: e.start + " 00:00" }, { xAxis: e.end + " 23:59" }]);
  panels.forEach((p, k) => {
    grid.push({ left: 132, right: 16, top: top + k * (ph + gap), height: ph });
    xAxis.push({ type: "time", gridIndex: k, min: P.start, max: P.end, axisLabel: { show: k === panels.length - 1, color: mute, hideOverlap: true },
      axisLine: { lineStyle: { color: line } }, axisTick: { show: k === panels.length - 1 }, splitLine: { show: false } });
  });
  P.series.forEach((s, k) => {
    const vals = [...s.mean, ...s.min, ...s.max].filter((v): v is number => v != null);
    let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
    const pd = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; lo -= pd; hi += pd;
    yAxis.push({ type: "value", gridIndex: k, min: +lo.toPrecision(4), max: +hi.toPrecision(4), splitNumber: 2, name: `${s.name}\n${s.unit}`, nameLocation: "middle", nameRotate: 0, nameGap: 56,
      nameTextStyle: { align: "right", color: ink, lineHeight: 16 }, axisLabel: { color: mute, fontSize: 11 }, splitLine: { lineStyle: { color: line } } });
    const areas: any[] = exAreas.map((a) => [{ ...a[0], itemStyle: { color: mute, opacity: 0.2 } }, a[1]]);
    if (flag) s.bad_spans.forEach(([a, b]) => areas.push([{ xAxis: a, itemStyle: { color: "#c92a2a", opacity: 0.22 } }, { xAxis: b }]));
    series.push({ type: "line", xAxisIndex: k, yAxisIndex: k, stack: "b" + k, data: P.t.map((t, i) => [t, s.min[i]]), lineStyle: { opacity: 0 }, symbol: "none", silent: true, tooltip: { show: false } });
    series.push({ type: "line", xAxisIndex: k, yAxisIndex: k, stack: "b" + k, data: P.t.map((t, i) => [t, s.max[i] != null && s.min[i] != null ? s.max[i]! - s.min[i]! : null]), lineStyle: { opacity: 0 }, symbol: "none", areaStyle: { color: "#d9480f", opacity: 0.14 }, silent: true, tooltip: { show: false } });
    series.push({ type: "line", name: s.name, xAxisIndex: k, yAxisIndex: k, data: P.t.map((t, i) => [t, s.mean[i]]), symbol: "none", lineStyle: { color: "#d9480f", width: 1.4 }, itemStyle: { color: "#d9480f" },
      markArea: { silent: true, data: areas } });
    if (flag && s.marks.length) series.push({ type: "scatter", name: s.name + " 剔除点", xAxisIndex: k, yAxisIndex: k, symbolSize: 6, itemStyle: { color: "#e67700" },
      data: s.marks.map(([t, v, kind]) => ({ value: [t, v == null ? hi : Math.max(lo, Math.min(hi, v))], raw: v, kind })),
      tooltip: { formatter: (p: any) => `${p.value[0]}<br/>${p.data.kind}：原始值 ${p.data.raw}` } });
  });
  if (withCk) {
    const k = P.series.length, ys = P.calib!.map((c) => c[1]);
    const lo = ys.length ? Math.min(...ys) : 0, hi = ys.length ? Math.max(...ys) : 1, pd = (hi - lo) * 0.08 || 5;
    yAxis.push({ type: "value", gridIndex: k, min: Math.floor(lo - pd), max: Math.ceil(hi + pd), splitNumber: 2, name: `校核值\n${P.target?.name.split(" ")[0] || ""} ${P.target?.unit || ""}`, nameLocation: "middle", nameRotate: 0, nameGap: 56,
      nameTextStyle: { align: "right", color: ink, lineHeight: 16 }, axisLabel: { color: mute, fontSize: 11 }, splitLine: { lineStyle: { color: line } } });
    series.push({ type: "scatter", name: "校核值", xAxisIndex: k, yAxisIndex: k, symbolSize: 4, itemStyle: { color: ink, opacity: 0.7 }, data: P.calib!.map((c) => [c[0], c[1]]),
      markArea: { silent: true, data: exAreas.map((a) => [{ ...a[0], itemStyle: { color: mute, opacity: 0.2 } }, a[1]]) } as any });
  }
  const all = panels.map((_, i) => i);
  return {
    animation: false, grid, xAxis, yAxis, series,
    axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: mute } },
    tooltip: { trigger: "axis", confine: true, valueFormatter: (v: any) => (typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3)) : v) },
    dataZoom: [{ type: "inside", xAxisIndex: all, filterMode: "none" }],
  };
}

/* ---------------- 训练用校核数据 ---------------- */
function CalibCard({ sid, sc, S, busy, putData, refresh }: { sid: number; sc: Scenario; S?: Summary; busy: boolean; putData: (b: Record<string, unknown>, k?: string[]) => Promise<boolean>; refresh: (k?: string[]) => Promise<unknown> }) {
  const toast = useToast();
  const g = sc.config.target as Target;
  const [msg, setMsg] = useState("");
  const onFile = async (f?: File) => {
    if (!f) return;
    try {
      const r = await api.upload<{ filename: string; rows: number; skipped: number; out_of_range: number; from: string; to: string }>(`/api/scenarios/${sid}/calib/import`, f);
      setMsg(`已读取 ${r.filename}：${r.rows} 条（${r.from} 至 ${r.to}）${r.skipped ? `，跳过 ${r.skipped} 条无数值记录` : ""}${r.out_of_range ? `，其中 ${r.out_of_range} 条超出合理范围` : ""}。审核通过后才参与训练。`);
      toast(`已导入 ${r.filename}，${r.rows} 条，待审核`);
      refresh(["scenario", "summary", "preview"]);
    } catch (e) { setMsg((e as Error).message); }
  };
  const review = async (iid: number, action: string) => {
    await api.post(`/api/calib-imports/${iid}/review`, { action });
    toast(action === "approve" ? "已审核通过，参与训练" : "已作废，不参与训练");
    refresh(["summary", "preview"]);
  };
  const C = S?.calib;
  const norm = C?.coverage.find((c) => c.id === "norm")?.n ?? 0, hi = C?.coverage.find((c) => c.id === "hi")?.n ?? 0;
  const others = C?.coverage.filter((c) => c.id !== "norm" && c.id !== "hi" && c.n > 0) || [];
  return (
    <div className="c s12"><h2>训练用校核数据 <span className="sub">带时间的炉管壁温测量值，用作训练和检验的"正确答案"；所有配对都按测量时刻</span></h2>
      <div className="row" style={{ marginBottom: 10 }}><span className="note">目标：<b>{g.name}</b>（在上方"估算目标"中设置，取法：{g.pick === "avg" ? "1#–8# 管按时刻平均" : "只用 1# 管"}）</span>
        <label className="tag t-mute" style={{ padding: "8px 12px", cursor: "pointer", borderRadius: 7, fontSize: 14, marginLeft: "auto" }}>导入校核数据（Excel / CSV）
          <input type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }} onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} /></label>
        <a href="/api/calib-template.csv" download><button>下载导入模板</button></a></div>
      {msg && <p className="note" style={{ margin: "0 0 8px" }}>{msg}</p>}
      {!C ? <Spinner /> : <div className={busy ? "loading" : ""}>
        <div className="scroll"><table className="stack"><thead><tr><th>使用</th><th>来源</th><th>覆盖时间</th><th className="r">所选时段内可用</th><th>测量位置</th><th>说明</th></tr></thead>
          <tbody>{C.sources.map((s) => (
            <tr key={s.id}><td><input type="checkbox" aria-label="使用" checked={s.use} disabled={s.status === "已作废"} onChange={(e) => putData({ calib_use: { [s.id]: e.target.checked } }, ["scenario", "summary", "preview"])} /></td>
              <td><b>{s.name}</b>{s.status !== "已审核" && <div style={{ marginTop: 4 }}>{s.status === "待审核" && s.import_id ? <span className="row"><span className="tag t-acc">待审核</span><button className="pri" style={{ minHeight: 28, padding: "2px 10px" }} onClick={() => review(s.import_id!, "approve")}>审核通过</button><button style={{ minHeight: 28, padding: "2px 10px" }} onClick={() => review(s.import_id!, "void")}>作废</button></span> : <span className="tag t-mute">{s.status}</span>}</div>}</td>
              <td style={{ whiteSpace: "nowrap" }}>{s.from ? `${s.from} 至 ${s.to}` : "—"}</td>
              <td className="r">{s.use ? fmtN(s.available) : "—"}{s.use && !s.training && <div className="note">仅作参考</div>}</td><td>{s.position}</td><td className="note">{s.note}</td></tr>))}</tbody></table></div>
        <div className="vimp" style={{ marginTop: 12 }}>
          <div>可用校核样本<b>{fmtN(C.total)}</b></div>
          <div>{C.pick === "avg" ? "热电偶（参考） / 红外样本" : "热电偶 / 红外"}<b>{fmtN(C.tc)} / {fmtN(C.ir)}</b></div>
          <div>覆盖正常稳定<b>{fmtN(norm)}</b></div><div>覆盖高负荷<b>{fmtN(hi)}</b></div>
          <div className="t">工况按"分工况"的规则判定（当前为模板默认规则）{others.length ? `；另有 ${others.map((o) => `${o.name} ${o.n} 个`).join("、")}` : ""}。烧焦、停炉期间的测量已按校核规则剔除。
            {C.warn && <span className="err"> 校核点偏少，训练结果可能不可靠，建议扩大时间范围或导入更多校核数据。</span>}</div>
        </div></div>}
    </div>
  );
}

/* ---------------- 数据处理规则 ---------------- */
const PARAMS: Record<string, [string, string, string][]> = {
  range: [["lo", "下限", ""], ["hi", "上限", ""]],
  freeze: [["minutes", "窗口", "min"], ["tol", "变化小于", ""]],
  spike: [["window", "参考窗口", "min"], ["jump", "偏离超过", ""]],
  fill: [["max_minutes", "最多补齐", "min"]],
  smooth: [["minutes", "窗口", "min"]],
  tc_fail: [["jump", "1 分钟跳变超过", ""]],
};

function RulesCard({ sid, sc, refresh }: { sid: number; sc: Scenario; refresh: (k?: string[]) => Promise<unknown> }) {
  const qc = useQueryClient();
  const toast = useToast();
  const rq = useQuery({ queryKey: ["rules", sid], queryFn: () => api.get<Rules>(`/api/scenarios/${sid}/rules`) });
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const R = rq.data;
  const item = R?.items.find((x) => x.key === key) || R?.items[0];
  const put = async (kind: string, patch: Record<string, unknown>) => {
    try {
      const r = await api.put<Rules>(`/api/scenarios/${sid}/rules/${item!.key}`, { rules: { [kind]: patch } });
      qc.setQueryData(["rules", sid], r);
      setErr("");
      refresh(["scenario", "summary", "preview"]);
    } catch (e) { setErr((e as Error).message); rq.refetch(); }
  };
  const save = async () => {
    const r = await api.post<Rules & { code: string }>(`/api/scenarios/${sid}/rules/save`);
    qc.setQueryData(["rules", sid], r);
    toast(`已保存 ${r.code}，原始数据不变，重新生成训练数据时生效`);
    refresh(["scenario", "overview"]);
  };
  return (
    <div className="c s5"><h2>数据处理规则 <span className="sub">按参数分别设置，物理范围默认取本体中的测点限值</span></h2>
      {!R || !item ? <Spinner text="加载中…" /> : <>
        <div className="row" style={{ marginBottom: 8 }}><select aria-label="选择参数" value={item.key} onChange={(e) => setKey(e.target.value)}>{R.items.map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}</select>
          <span className="note">当前版本 {R.code}{R.dirty ? "（有未保存的改动）" : ""}</span></div>
        {item.rules.map((r) => (
          <div className="rule" key={r.kind} style={r.params.on ? undefined : { opacity: 0.55 }}>
            <b>{r.title}</b><span className="note">{r.desc}</span>
            {r.src === "本体" ? <span className="tag t-teal">来自本体</span> : r.src === "估算目标" ? <span className="tag t-teal">来自估算目标</span> : r.src === "手工" ? <span className="tag t-warn">手工修改</span> : null}
            {item.key !== "__calib__" || r.kind !== "align" ? <label className="x"><input type="checkbox" checked={!!r.params.on} onChange={(e) => put(r.kind, { on: e.target.checked })} /> 启用</label> : null}
            {PARAMS[r.kind] && r.params.on && <div className="rparams">{PARAMS[r.kind].map(([k, lab, u]) => (
              <label key={k} className="row" style={{ gap: 4 }}><span className="note">{lab}</span>
                <NumInput value={r.params[k]} onCommit={(v) => put(r.kind, { [k]: v })} /><span className="note">{u || item.unit}</span></label>))}</div>}
            <div className="ract"><span className="note">触发后</span>{r.actions.length > 1 ?
              <select aria-label={r.title + " 触发后处理"} value={r.params.action || 0} onChange={(e) => put(r.kind, { action: +e.target.value })}>{r.actions.map((a, j) => <option key={j} value={j}>{a}</option>)}</select>
              : <span>{r.actions[0]}</span>}</div>
          </div>))}
        <p className="note err">{err}</p>
        <div className="row" style={{ marginTop: 8 }}><button className="pri" disabled={!R.dirty} onClick={save}>{R.dirty ? `保存为 ${R.next} 并重新处理` : `规则已保存为 ${R.code}`}</button></div>
        <p className="note">原始数据保留不动，规则只产生质量标记；改了规则后上面的诊断和影响比例立即按新规则重算。所有判定只用当前及以前的数据，训练和上线一致。</p>
      </>}
    </div>
  );
}

function NumInput({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => { const n = parseFloat(v); if (!isNaN(n) && n !== value) onCommit(n); else setV(String(value)); };
  return <input type="text" inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />;
}

/* ---------------- 生成训练数据（数据版本） ---------------- */
function GenCard({ sid, S, busy }: { sid: number; S?: Summary; busy: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const dq = useQuery({ queryKey: ["datasets", sid], queryFn: () => api.get<DS[]>(`/api/scenarios/${sid}/datasets`) });
  const [running, setRunning] = useState(false);
  const gen = async () => {
    setRunning(true);
    try {
      const ds = await api.post<DS>(`/api/scenarios/${sid}/datasets`);
      toast(`${ds.code} 已生成，可在"训练模型"中使用`);
      ["datasets", "rules", "overview", "scenario"].forEach((k) => qc.invalidateQueries({ queryKey: [k, sid] }));
    } catch (e) { toast((e as Error).message); } finally { setRunning(false); }
  };
  return (
    <div className="c s12"><h2>生成训练数据 <span className="sub">保存参数清单、时间范围、排除时段、规则版本、校核来源和质量标记，生成数据版本 DS-vN</span></h2>
      <div className="row"><span className={busy ? "loading" : ""}>{S ? <><b>{S.gen.n_tags}</b> 个参数 · <b>{S.gen.start}</b> 至 <b>{S.gen.end}</b>（有效 {S.gen.eff_days} 天）· 校核样本 <b>{fmtN(S.gen.calib_points)}</b> 个 · 质量剔除约 {S.impact.lost_pct.toFixed(1)}% 的时间点</> : "计算中…"}</span>
        <button className="pri" style={{ marginLeft: "auto" }} disabled={running} onClick={gen}>{running ? "生成中…" : "生成训练数据"}</button></div>
      <div className="scroll" style={{ marginTop: 10 }}><table className="stack"><thead><tr><th>版本</th><th>时间范围</th><th className="r">参数</th><th className="r">校核样本</th><th>处理规则</th><th>生成时间</th><th>被使用</th></tr></thead>
        <tbody>{dq.data?.length ? dq.data.map((r) => (
          <tr key={r.code}><td><b>{r.code}</b></td><td>{r.start} 至 {r.end}</td><td className="r">{r.n_tags}</td><td className="r">{fmtN(r.calib_points)}</td><td>{r.rule_code}</td><td>{r.created_at}</td><td>{r.used_by.length ? r.used_by.join("、") : "—"}</td></tr>))
          : <tr><td colSpan={7} className="note" style={{ textAlign: "center", padding: 18 }}>还没有数据版本</td></tr>}</tbody></table></div>
    </div>
  );
}
