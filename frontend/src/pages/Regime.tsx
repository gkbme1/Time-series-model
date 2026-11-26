import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ECharts, EChartsOption } from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { EChart } from "../components/EChart";
import { cssVar, Spinner, useToast } from "../ui";

type Cls = { id: string; name: string; color: string; include: boolean };
type Cond = { param: string; op: string; v: number | string; v2?: number | string; hours?: number | string };
type Rule = { regime: string; all: boolean; min_hours: number | string; conds: Cond[] };
type Auto = { a: string; b: string; desc: string; rw: string; w: string; st: string };
type Man = { a: string; b: string; w: string; who: string };
type Cycle = { i: number; start: string; end: string; days: number; why: string; done: boolean; pre: boolean };
type RG = {
  classes: Cls[]; rules: Rule[]; manual: Man[]; auto: Auto[]; auto_keys: string[]; show: string[]; sens: string;
  cycle_mode: string; cycle0_start: string | null; params: { name: string; tag: string; unit: string }[]; ops: Record<string, string>;
  summary: (Cls & { minutes: number; pct: number; sources: Record<string, number> })[]; cycles: Cycle[];
  code: string | null; dirty: boolean; next: string; data_start: string; data_end: string;
};
type CD = {
  t: string[]; step_minutes: number; start: string; end: string; excludes: { start: string; end: string }[];
  series: { name: string; unit: string; mean: (number | null)[]; min: (number | null)[]; max: (number | null)[] }[];
  runs: [string, string, string][]; overview: { start: string; end: string; runs: [string, string, string][] };
};

const pad = (n: number) => String(n).padStart(2, "0");
const fmtT = (ms: number) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const ms = (s: string) => Date.parse(s.replace(" ", "T"));
const durTxt = (min: number) => (min >= 48 * 60 ? (min / 1440).toFixed(1) + " 天" : min >= 60 ? (min / 60).toFixed(min % 60 ? 1 : 0) + " h" : min + " min");
const CYC_COL = ["#4c6ef5", "#12b886", "#fab005", "#e64980", "#868e96", "#15aabf"];
const OPS: [string, string][] = [[">", "大于"], ["<", "小于"], ["between", "介于"], ["rise", "上升超过"], ["fall", "下降超过"]];

export default function RegimePage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const rq = useQuery({ queryKey: ["regime", sid], queryFn: () => api.get<RG>(`/api/scenarios/${sid}/regime`) });
  const [win, setWin] = useState<[string, string] | null>(null);
  const [foc, setFoc] = useState<{ a: string; b: string } | null>(null);
  const [mode, setMode] = useState<"browse" | "brush">("browse");
  const [pop, setPop] = useState<{ a: string; b: string } | null>(null);
  const [tab, setTab] = useState<"rule" | "man" | "auto">("rule");
  const R = rq.data;
  const set = (d: RG) => { qc.setQueryData(["regime", sid], d); qc.invalidateQueries({ queryKey: ["rchart", sid] }); qc.invalidateQueries({ queryKey: ["overview", sid] }); };
  const call = async (p: Promise<RG>, ok?: string) => { try { const d = await p; set(d); if (ok) toast(ok); return d; } catch (e) { toast((e as Error).message); } };

  const cq = useQuery({
    queryKey: ["rchart", sid, R?.show.join(","), win?.join("~") || ""],
    queryFn: () => api.get<CD>(`/api/scenarios/${sid}/regime/chart?params=${encodeURIComponent(R!.show.join(","))}&buckets=800` + (win ? `&start=${encodeURIComponent(win[0])}&end=${encodeURIComponent(win[1])}` : "")),
    enabled: !!R, placeholderData: (p) => p,
  });
  if (!R) return <Spinner text="加载中…" />;
  const cls = (id: string) => R.classes.find((c) => c.id === id) || { id, name: id, color: "#888", include: false };
  const C = cq.data;

  const zoom = (f: number) => {
    if (!C) return;
    const a = ms(C.start), b = ms(C.end), c = foc ? (ms(foc.a) + ms(foc.b)) / 2 : (a + b) / 2, s = (b - a) * f;
    setWin([fmtT(c - s / 2), fmtT(c + s / 2)]);
  };
  const panBy = (f: number) => { if (!C) return; const a = ms(C.start), b = ms(C.end), s = (b - a) * f; setWin([fmtT(a + s), fmtT(b + s)]); };
  const focusOn = (x: { a: string; b: string }) => {
    setFoc(x);
    const a = ms(x.a), b = ms(x.b), mid = (a + b) / 2, span = Math.max(24 * 3600e3, (b - a) * 2.5);
    setWin([fmtT(mid - span / 2), fmtT(mid + span / 2)]);
    document.getElementById("wFig")?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const toggleShow = (n: string) => {
    const s = R.show.includes(n) ? (R.show.length > 1 ? R.show.filter((x) => x !== n) : R.show) : R.show.length >= 5 ? (toast("最多同时显示 5 个参数"), R.show) : [...R.show, n];
    call(api.put<RG>(`/api/scenarios/${sid}/regime`, { show: s }));
  };
  const chipNames = Array.from(new Set([...R.rules.flatMap((r) => r.conds.map((c) => c.param)), ...R.show, ...R.auto_keys]));
  const newAuto = R.auto.filter((a) => a.st === "new").length;

  return (
    <div className="grid">
      <div className="c s12" style={{ padding: "10px 16px" }}><div className="row">
        <span className="note">工况方案</span><b>{R.code || "尚未保存"}</b>{R.dirty ? <span className="tag t-warn">有未保存的改动</span> : <span className="tag t-ok">已保存</span>}
        <span className="note">规则、手动标注、确认的自动发现、纳入训练的工况和周期设置一起保存为一个版本，训练时引用</span>
        <button className="pri" style={{ marginLeft: "auto" }} disabled={!R.dirty} onClick={async () => { const d = await call(api.post<RG & { code: string }>(`/api/scenarios/${sid}/regime/save`)); if (d) toast(`已保存工况方案 ${(d as any).code}`); qc.invalidateQueries({ queryKey: ["projects"] }); }}>{R.dirty ? `保存为 ${R.next}` : "已保存"}</button></div></div>

      <div className="c s12"><h2>工况判定结果 <span className="sub">{C ? `显示 ${C.start} 至 ${C.end}` : ""}{cq.isFetching ? " · 加载中…" : ""}</span></h2>
        <div className="row wtool">
          <div className="seg" role="group" aria-label="操作模式"><button className={mode === "browse" ? "on" : ""} onClick={() => setMode("browse")}>浏览</button><button className={mode === "brush" ? "on" : ""} onClick={() => setMode("brush")}>框选标注</button></div>
          <span className="row" style={{ gap: 4 }}><button aria-label="放大" onClick={() => zoom(0.5)}>＋</button><button aria-label="缩小" onClick={() => zoom(2)}>－</button><button onClick={() => setWin(null)}>全部</button>
            <button disabled={!foc} onClick={() => foc && focusOn(foc)}>适配选中</button><button aria-label="向前" onClick={() => panBy(-0.5)}>◀</button><button aria-label="向后" onClick={() => panBy(0.5)}>▶</button></span>
          {foc && <span className="tag t-acc" style={{ padding: "4px 6px 4px 10px", fontSize: 13 }}>已选中 {foc.a} 至 {foc.b}<button aria-label="取消选中" style={{ minHeight: 24, padding: "0 6px", marginLeft: 6, border: 0, background: "none", color: "inherit" }} onClick={() => setFoc(null)}>×</button></span>}
          <span className="note">{mode === "brush" ? "按住拖动选出时段，松开后指定工况" : "滚轮缩放，拖动平移；下方总览条可点击跳转"}</span>
        </div>
        <div className="chips" style={{ marginTop: 8 }}>{chipNames.map((n) => <button key={n} className={"chip" + (R.show.includes(n) ? " on" : "")} onClick={() => toggleShow(n)}>{n}</button>)}
          <select aria-label="添加参数" value="" onChange={(e) => e.target.value && toggleShow(e.target.value)} style={{ minHeight: 32, fontSize: 13, padding: "2px 8px" }}><option value="">＋ 其他参数</option>{R.params.filter((p) => !chipNames.includes(p.name)).map((p) => <option key={p.tag} value={p.name}>{p.name}</option>)}</select></div>
        <div id="wFig" className={cq.isFetching ? "loading" : ""}>{C ? <RegimeChart C={C} cls={cls} foc={foc} mode={mode} onWin={setWin} onBrush={(a, b) => setPop({ a, b })} /> : <Spinner />}</div>
        {C && <Overview C={C} cls={cls} onJump={(c) => { const a = ms(C.start), b = ms(C.end), s = b - a; setWin([fmtT(c - s / 2), fmtT(c + s / 2)]); }} />}
        {pop && <BrushPop pop={pop} classes={R.classes} onCancel={() => setPop(null)} onOk={async (w, name) => {
          const d = await call(api.post<RG>(`/api/scenarios/${sid}/regime/manual`, { a: pop.a, b: pop.b, w, name }));
          if (d) { toast(`已标为"${name || cls(w).name}"`); setPop(null); }
        }} />}
        <div className="legend">{R.classes.map((c) => <span key={c.id}><i style={{ background: c.color, opacity: 0.5, height: 10 }} />{c.name}</span>)}</div>
      </div>

      <div className="c s12">
        <div className="tabs" role="tablist">
          <button className={"tab" + (tab === "rule" ? " on" : "")} onClick={() => setTab("rule")}>按规则判定</button>
          <button className={"tab" + (tab === "man" ? " on" : "")} onClick={() => setTab("man")}>手动框选 <span className="note">{R.manual.length ? `(${R.manual.length})` : ""}</span></button>
          <button className={"tab" + (tab === "auto" ? " on" : "")} onClick={() => setTab("auto")}>自动发现 {newAuto > 0 && <span className="tag t-acc">{newAuto} 待确认</span>}</button>
        </div>
        {tab === "rule" && <RuleEditor R={R} onApply={(rules) => call(api.put<RG>(`/api/scenarios/${sid}/regime`, { rules }), "已按规则重新判定")} />}
        {tab === "man" && <>
          <p className="note" style={{ margin: "0 0 10px" }}>在上图切换到"框选标注"，按住拖动选出时段后指定工况。手动标注优先于规则和自动发现。</p>
          <div className="scroll"><table className="stack"><thead><tr><th>时段</th><th className="r">时长</th><th>工况</th><th>标注人</th><th></th></tr></thead>
            <tbody>{R.manual.length ? R.manual.map((m, i) => (
              <tr key={i} className={foc?.a === m.a && foc?.b === m.b ? "sel" : ""}><td style={{ whiteSpace: "nowrap" }}><a className="link" onClick={() => focusOn(m)}>{m.a} 至 {m.b}</a></td>
                <td className="r">{durTxt((ms(m.b) - ms(m.a)) / 60e3)}</td><td><span className="dot" style={{ background: cls(m.w).color }} />{cls(m.w).name}</td><td>{m.who}</td>
                <td><button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => call(fetch(`/api/scenarios/${sid}/regime/manual/${i}`, { method: "DELETE" }).then((r) => r.json()), "已删除手动标注")}>删除</button></td></tr>))
              : <tr><td colSpan={5} className="note" style={{ textAlign: "center", padding: 18 }}>还没有手动标注</td></tr>}</tbody></table></div></>}
        {tab === "auto" && <AutoTab R={R} sid={sid} cls={cls} foc={foc} focusOn={focusOn} call={call} />}
      </div>

      <div className="c s12"><h2>工况汇总 <span className="sub">{R.data_start} 至 {R.data_end}（扣除排除时段）</span></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>工况</th><th className="r">时长</th><th style={{ width: "26%" }}>占比</th><th>判定来源</th><th>纳入训练</th></tr></thead>
          <tbody>{R.summary.map((w) => (
            <tr key={w.id}><td><span className="dot" style={{ background: w.color }} /><b>{w.name}</b></td><td className="r">{durTxt(w.minutes)}</td>
              <td><div className="row" style={{ flexWrap: "nowrap" }}><div className="bar" style={{ flex: 1 }}><b style={{ width: `${w.pct}%`, background: w.color }} /></div><span className="note" style={{ width: 50, textAlign: "right" }}>{w.pct.toFixed(1)}%</span></div></td>
              <td className="note">{Object.entries(w.sources).map(([k, v]) => `${k} ${durTxt(v)}`).join(" · ") || "—"}</td>
              <td><input type="checkbox" aria-label={"纳入 " + w.name} checked={w.include} onChange={(e) => call(api.put<RG>(`/api/scenarios/${sid}/regime`, { classes: [{ id: w.id, include: e.target.checked }] }))} /></td></tr>))}</tbody></table></div>
        <p className="note">未纳入训练的工况，模型上线后遇到时不输出结果。</p></div>

      <Cycles R={R} sid={sid} call={call} />
    </div>
  );
}

/* ---------------- 工况图 ---------------- */
function RegimeChart({ C, cls, foc, mode, onWin, onBrush }: { C: CD; cls: (id: string) => Cls; foc: { a: string; b: string } | null; mode: string; onWin: (w: [string, string]) => void; onBrush: (a: string, b: string) => void }) {
  const t = useRef<number>();
  const chartRef = useRef<ECharts>();
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const option = useMemo<EChartsOption>(() => {
    const ink = cssVar("--ink"), mute = cssVar("--mute"), line = cssVar("--line");
    const n = C.series.length, ph = 82, gap = 12;
    const grid: any[] = [], xAxis: any[] = [], yAxis: any[] = [], series: any[] = [];
    const areas = C.runs.map(([a, b, w]) => [{ xAxis: a, itemStyle: { color: cls(w).color, opacity: 0.14 } }, { xAxis: b }]);
    if (foc) areas.push([{ xAxis: foc.a, itemStyle: { color: "#d9480f", opacity: 0.1, borderColor: ink, borderWidth: 2, borderType: "dashed" } } as any, { xAxis: foc.b }]);
    C.series.forEach((s, k) => {
      grid.push({ left: 132, right: 16, top: 8 + k * (ph + gap), height: ph });
      xAxis.push({ type: "time", gridIndex: k, min: C.start, max: C.end, axisLabel: { show: k === n - 1, color: mute, hideOverlap: true }, axisLine: { lineStyle: { color: line } }, splitLine: { show: false } });
      const v = [...s.mean, ...s.min, ...s.max].filter((x): x is number => x != null);
      let lo = v.length ? Math.min(...v) : 0, hi = v.length ? Math.max(...v) : 1; const pd = (hi - lo) * 0.08 || 1; lo -= pd; hi += pd;
      yAxis.push({ type: "value", gridIndex: k, min: +lo.toPrecision(4), max: +hi.toPrecision(4), splitNumber: 2, name: `${s.name}\n${s.unit}`, nameLocation: "middle", nameRotate: 0, nameGap: 56,
        nameTextStyle: { align: "right", color: ink, lineHeight: 16 }, axisLabel: { color: mute, fontSize: 11 }, splitLine: { lineStyle: { color: line } } });
      series.push({ type: "line", name: s.name, xAxisIndex: k, yAxisIndex: k, data: C.t.map((x, i) => [x, s.mean[i]]), symbol: "none", lineStyle: { color: "#d9480f", width: 1.3 }, itemStyle: { color: "#d9480f" }, markArea: { silent: true, data: areas } });
    });
    const all = C.series.map((_, i) => i);
    return {
      animation: false, grid, xAxis, yAxis, series, axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: { trigger: "axis", confine: true, valueFormatter: (v: any) => (typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3)) : v) },
      dataZoom: [{ type: "inside", xAxisIndex: all, filterMode: "none", disabled: mode === "brush" }],
      brush: { xAxisIndex: "all", brushLink: "all", throttleType: "debounce", transformable: false, brushStyle: { color: "rgba(217,72,15,.16)", borderColor: "#d9480f", borderWidth: 1 }, outOfBrush: { colorAlpha: 1 } },
      toolbox: { show: false, feature: { brush: { type: ["lineX"] } } },
    } as EChartsOption;
  }, [C, foc, mode]);
  const height = Math.max(200, C.series.length * 94 + 34);
  return <EChart option={option} height={height}
    onInit={(c) => {
      chartRef.current = c;
      c.dispatchAction({ type: "takeGlobalCursor", key: "brush", brushOption: modeRef.current === "brush" ? { brushType: "lineX", brushMode: "single" } : { brushType: false } } as any);
    }}
    onEvents={{
      datazoom: (_p, chart) => {
        if (modeRef.current !== "browse") return;
        clearTimeout(t.current);
        t.current = window.setTimeout(() => {
          const dz = (chart.getOption() as any).dataZoom?.[0];
          if (dz && dz.startValue != null && (dz.start > 0.5 || dz.end < 99.5)) onWin([fmtT(dz.startValue), fmtT(dz.endValue)]);
        }, 350);
      },
      brushEnd: (p, chart) => {
        const r = p.areas?.[0]?.coordRange;
        chart.dispatchAction({ type: "brush", areas: [] } as any);
        if (!r) return;
        const [a, b] = (Array.isArray(r[0]) ? r[0] : r) as number[];
        if (Math.abs(b - a) < 10 * 60e3) return;
        const round = (x: number) => Math.round(x / 600e3) * 600e3;
        onBrush(fmtT(round(Math.min(a, b))), fmtT(round(Math.max(a, b))));
      },
    }} />;
}

function Overview({ C, cls, onJump }: { C: CD; cls: (id: string) => Cls; onJump: (centerMs: number) => void }) {
  const option = useMemo<EChartsOption>(() => {
    const mute = cssVar("--mute"), ink = cssVar("--ink");
    const areas: any[] = C.overview.runs.map(([a, b, w]) => [{ xAxis: a, itemStyle: { color: cls(w).color, opacity: 0.55 } }, { xAxis: b }]);
    areas.push([{ xAxis: C.start, itemStyle: { color: "transparent", borderColor: ink, borderWidth: 2 } }, { xAxis: C.end }]);
    return {
      animation: false, grid: { left: 132, right: 16, top: 2, bottom: 20 },
      xAxis: { type: "time", min: C.overview.start, max: C.overview.end, axisLabel: { color: mute, fontSize: 11 }, splitLine: { show: false } },
      yAxis: { type: "value", show: false, min: 0, max: 1 },
      series: [{ type: "line", data: [[C.overview.start, 0.5]], symbol: "none", lineStyle: { opacity: 0 }, markArea: { silent: true, data: areas } }],
    };
  }, [C]);
  return <div style={{ cursor: "pointer", marginTop: 4 }}><EChart option={option} height={48} onInit={(c) => {
    const zr = c.getZr() as any;
    if (zr.__bound) return;
    zr.__bound = true;
    zr.on("click", (e: any) => { const v = c.convertFromPixel({ xAxisIndex: 0 } as any, [e.offsetX, e.offsetY]) as any; const x = Array.isArray(v) ? v[0] : v; if (x) onJump(+x); });
  }} /></div>;
}

function BrushPop({ pop, classes, onOk, onCancel }: { pop: { a: string; b: string }; classes: Cls[]; onOk: (w: string, name?: string) => void; onCancel: () => void }) {
  const [w, setW] = useState(classes[0]?.id || "norm");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="wpop show">
      <b>已选 {pop.a} 至 {pop.b}</b><span className="note">（{durTxt((ms(pop.b) - ms(pop.a)) / 60e3)}）</span><span>标为</span>
      <select aria-label="工况" value={w} onChange={(e) => setW(e.target.value)}>{classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}<option value="__new">新建工况…</option></select>
      {w === "__new" && <input type="text" placeholder="新工况名称" style={{ width: 140 }} value={name} onChange={(e) => setName(e.target.value)} />}
      <button className="pri" onClick={() => { if (w === "__new" && !name.trim()) { setErr("请输入新工况名称"); return; } onOk(w === "__new" ? "" : w, w === "__new" ? name.trim() : undefined); }}>确定</button>
      <button onClick={onCancel}>取消</button><span className="note err">{err}</span>
    </div>
  );
}

/* ---------------- 规则编辑 ---------------- */
function RuleEditor({ R, onApply }: { R: RG; onApply: (rules: Rule[]) => void }) {
  const [rules, setRules] = useState<Rule[]>(() => JSON.parse(JSON.stringify(R.rules)));
  const [changed, setChanged] = useState(false);
  useEffect(() => { setRules(JSON.parse(JSON.stringify(R.rules))); setChanged(false); }, [R.rules]);
  const upd = (f: (r: Rule[]) => void) => { const c = JSON.parse(JSON.stringify(rules)); f(c); setRules(c); setChanged(true); };
  const unit = (p: string) => R.params.find((x) => x.name === p)?.unit || "";
  const sm = { minHeight: 32, padding: "2px 10px" };
  const opts = R.classes.filter((c) => c.id !== "norm");
  return (<>
    <p className="note" style={{ margin: "0 0 10px" }}>从上到下依次判断，先满足的生效；都不满足时为"正常稳定"。判定用的参数不必是模型输入。所有条件只用当前及以前的数据。</p>
    {rules.map((r, ri) => (
      <div className="wr" key={ri}>
        <div className="hd"><span className="dot" style={{ background: R.classes.find((c) => c.id === r.regime)?.color }} />
          <select aria-label="工况" value={r.regime} onChange={(e) => upd((c) => { c[ri].regime = e.target.value; })}>{opts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <span className="note">满足</span><select aria-label="组合方式" value={r.all ? "1" : "0"} onChange={(e) => upd((c) => { c[ri].all = e.target.value === "1"; })}><option value="1">全部条件（且）</option><option value="0">任一条件（或）</option></select>
          <span className="note">且持续 ≥</span><input type="text" inputMode="decimal" style={{ width: 56, minHeight: 34, padding: "3px 8px" }} value={r.min_hours} onChange={(e) => upd((c) => { c[ri].min_hours = e.target.value; })} aria-label="最短持续小时" /><span className="note">小时</span>
          {ri > 0 && <button style={sm} title="提高优先级" onClick={() => upd((c) => { [c[ri - 1], c[ri]] = [c[ri], c[ri - 1]]; })}>上移</button>}
          <button style={{ ...sm, marginLeft: "auto" }} onClick={() => upd((c) => { c.splice(ri, 1); })}>删除</button></div>
        {r.conds.map((cd, ci) => (
          <div className="wc" key={ci}>
            <select aria-label="参数" value={cd.param} onChange={(e) => upd((c) => { c[ri].conds[ci].param = e.target.value; })}>{R.params.map((p) => <option key={p.tag} value={p.name}>{p.name}</option>)}</select>
            <select aria-label="条件" value={cd.op} onChange={(e) => upd((c) => { const x = c[ri].conds[ci]; x.op = e.target.value; if (x.op === "between" && x.v2 == null) x.v2 = x.v; if ((x.op === "rise" || x.op === "fall") && x.hours == null) x.hours = 1; })}>{OPS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}</select>
            <input type="text" inputMode="decimal" aria-label="数值" value={cd.v} onChange={(e) => upd((c) => { c[ri].conds[ci].v = e.target.value; })} />
            {cd.op === "between" && <><span>至</span><input type="text" inputMode="decimal" aria-label="上限" value={cd.v2 ?? ""} onChange={(e) => upd((c) => { c[ri].conds[ci].v2 = e.target.value; })} /></>}
            <span className="note">{unit(cd.param)}</span>
            {(cd.op === "rise" || cd.op === "fall") && <><span className="note">（在</span><input type="text" inputMode="decimal" style={{ width: 50 }} aria-label="小时数" value={cd.hours ?? 1} onChange={(e) => upd((c) => { c[ri].conds[ci].hours = e.target.value; })} /><span className="note">小时内）</span></>}
            <button aria-label="删除条件" style={{ minHeight: 30, padding: "2px 8px" }} onClick={() => upd((c) => { c[ri].conds.splice(ci, 1); })}>×</button>
            {ci < r.conds.length - 1 && <span className="tag t-mute">{r.all ? "且" : "或"}</span>}
          </div>))}
        <button style={{ ...sm, marginTop: 4 }} onClick={() => upd((c) => { c[ri].conds.push({ param: R.params.find((p) => p.name === "出口温度")?.name || R.params[0].name, op: ">", v: 850 }); })}>＋ 添加条件</button>
      </div>))}
    <div className="row" style={{ marginTop: 10 }}>
      <button onClick={() => upd((c) => { c.push({ regime: opts.find((x) => x.id === "sw")?.id || opts[0].id, all: true, min_hours: 1, conds: [{ param: R.params.find((p) => p.name === "原料密度")?.name || R.params[0].name, op: "rise", v: 5, hours: 6 }] }); })}>新增工况规则</button>
      {changed && <button onClick={() => { setRules(JSON.parse(JSON.stringify(R.rules))); setChanged(false); }}>放弃改动</button>}
      <button className="pri" style={{ marginLeft: "auto" }} onClick={() => onApply(rules)}>{changed ? "应用规则（有改动）" : "应用规则"}</button></div>
  </>);
}

/* ---------------- 自动发现 ---------------- */
function AutoTab({ R, sid, cls, foc, focusOn, call }: { R: RG; sid: number; cls: (id: string) => Cls; foc: { a: string; b: string } | null; focusOn: (x: { a: string; b: string }) => void; call: (p: Promise<RG>, ok?: string) => Promise<RG | undefined> }) {
  const [keys, setKeys] = useState<string[]>(R.auto_keys);
  const [sens, setSens] = useState(R.sens);
  const [running, setRunning] = useState(false);
  const [pick, setPick] = useState<Record<number, string>>({});
  const [names, setNames] = useState<Record<number, string>>({});
  const cand = Array.from(new Set([...R.auto_keys, "出口温度", "燃料气流量", "原料流量", "原料密度", "烧焦空气阀开度"])).filter((n) => R.params.some((p) => p.name === n));
  const nNew = R.auto.filter((a) => a.st === "new").length, nMan = R.auto.filter((a) => a.st === "man").length;
  const act = (i: number, action: string) => {
    const w = pick[i] ?? R.auto[i].w;
    if (action === "ok" && w === "__new" && !names[i]?.trim()) return;
    call(api.post<RG>(`/api/scenarios/${sid}/regime/auto/${i}`, { action, w: w === "__new" ? undefined : w, name: w === "__new" ? names[i] : undefined }),
      action === "ok" ? `已确认为"${w === "__new" ? names[i] : cls(w).name}"` : action === "no" ? "已忽略" : "已撤销");
  };
  return (<>
    <div className="row" style={{ marginBottom: 10 }}><span className="note">盯住这些参数</span>
      <span className="chips" style={{ margin: 0 }}>{cand.map((n) => <button key={n} className={"chip" + (keys.includes(n) ? " on" : "")} onClick={() => setKeys(keys.includes(n) ? keys.filter((x) => x !== n) : [...keys, n])}>{n}</button>)}</span>
      <span className="note">灵敏度</span><select aria-label="灵敏度" value={sens} onChange={(e) => setSens(e.target.value)}><option value="high">高</option><option value="mid">中</option><option value="low">低</option></select>
      <button className="pri" style={{ marginLeft: "auto" }} disabled={running || !keys.length} onClick={async () => { setRunning(true); await call(api.post<RG>(`/api/scenarios/${sid}/regime/auto/run`, { keys, sens })); setRunning(false); }}>{running ? "发现中…" : "开始发现"}</button></div>
    <p className="note" style={{ margin: "0 0 8px" }}>{R.auto.length ? `找到 ${nNew} 个待确认的波动时段${nMan ? `（${nMan} 个已有手动标注）` : ""}。逐个确认是什么工况，确认后才生效。` : "程序找出这些参数明显波动的时段（3 小时变化量相对正常波动的倍数超过阈值），逐个确认是什么工况；确认后才生效。"}</p>
    <div className="scroll"><table className="stack"><thead><tr><th>时段</th><th>发生了什么</th><th>规则当前判定</th><th style={{ width: "30%" }}>确认为</th><th></th></tr></thead>
      <tbody>{R.auto.length ? R.auto.map((c, i) => {
        const w = pick[i] ?? c.w;
        return (<tr key={i} className={foc?.a === c.a && foc?.b === c.b ? "sel" : ""}>
          <td style={{ whiteSpace: "nowrap" }}><a className="link" onClick={() => focusOn(c)}>{c.a}<br />{c.b}</a></td><td>{c.desc}</td>
          <td style={{ whiteSpace: "nowrap" }}><span className="dot" style={{ background: cls(c.rw).color }} />{cls(c.rw).name}</td>
          <td>{c.st === "new" ? <div className="row" style={{ flexWrap: "nowrap" }}><select aria-label="确认为" value={w} onChange={(e) => setPick({ ...pick, [i]: e.target.value })}>{R.classes.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}<option value="__new">新建工况…</option></select>
            {w === "__new" && <input type="text" placeholder="新工况名称" style={{ width: 110, minHeight: 34, padding: "3px 8px" }} value={names[i] || ""} onChange={(e) => setNames({ ...names, [i]: e.target.value })} />}</div>
            : c.st === "ok" ? <span className="tag t-ok">已确认：{cls(c.w).name}</span> : c.st === "man" ? <span className="tag t-mute">已有手动标注</span> : <span className="tag t-mute">已忽略</span>}</td>
          <td style={{ whiteSpace: "nowrap" }}>{c.st === "new" ? <div className="row" style={{ flexWrap: "nowrap" }}><button className="pri" onClick={() => act(i, "ok")}>确认</button><button onClick={() => act(i, "no")}>忽略</button></div>
            : c.st === "ok" || c.st === "no" ? <button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => act(i, "undo")}>撤销</button> : null}</td></tr>);
      }) : <tr><td colSpan={5} className="note" style={{ textAlign: "center", padding: 18 }}>选好参数后点"开始发现"</td></tr>}</tbody></table></div>
  </>);
}

/* ---------------- 运行周期 ---------------- */
function Cycles({ R, sid, call }: { R: RG; sid: number; call: (p: Promise<RG>, ok?: string) => Promise<RG | undefined> }) {
  const cs = R.cycles;
  const a0 = ms(R.data_start + " 00:00"), b0 = ms(R.data_end + " 23:59");
  const full = cs.filter((c) => c.done).length;
  const [c0, setC0] = useState(R.cycle0_start?.slice(0, 10) || "");
  useEffect(() => setC0(R.cycle0_start?.slice(0, 10) || ""), [R.cycle0_start]);
  const pos = (s: string) => Math.max(0, Math.min(100, ((ms(s) - a0) / (b0 - a0)) * 100));
  return (
    <div className="c s12"><h2>运行周期 <span className="sub">两次重置事件之间的连续运行段，用于按周期划分训练 / 检验数据和计算累计量</span></h2>
      <div className="row"><span className="note">周期边界</span>
        <select aria-label="周期边界" value={R.cycle_mode} onChange={(e) => call(api.put<RG>(`/api/scenarios/${sid}/regime`, { cycle_mode: e.target.value }))}>
          <option value="both">烧焦或停炉结束 → 下一次烧焦或停炉开始</option><option value="burn">仅以烧焦为边界</option><option value="none">不划分周期（如精馏塔等无重置事件的装置）</option></select>
        {R.cycle_mode !== "none" && <><span className="note" style={{ marginLeft: 12 }}>数据起点之前最近一次烧焦结束</span>
          <input type="date" className="din" value={c0} onChange={(e) => setC0(e.target.value)} onBlur={() => call(api.put<RG>(`/api/scenarios/${sid}/regime`, { cycle0_start: c0 ? c0 + " 00:00" : "" }))} />
          <span className="note">（按运行记录填写；不填则第一个周期从数据起点算起）</span></>}</div>
      {cs.length > 0 && <div className="cystrip"><div className="cys">{cs.map((c) => (
        <i key={c.i} style={{ left: `${pos(c.start)}%`, width: `${pos(c.end) - pos(c.start)}%`, background: CYC_COL[(c.i - 1) % CYC_COL.length] }} title={`周期 ${c.i}`}>周期 {c.i}</i>))}</div>
        <div className="note cyx"><span>{R.data_start.slice(5)}</span><span>{R.data_end.slice(5)}</span></div></div>}
      <div className="scroll"><table className="stack"><thead><tr><th>周期</th><th>开始</th><th>结束</th><th className="r">天数</th><th>结束原因</th><th>状态</th></tr></thead>
        <tbody>{cs.length ? cs.map((c) => (
          <tr key={c.i}><td><span className="dot" style={{ background: CYC_COL[(c.i - 1) % CYC_COL.length] }} /><b>周期 {c.i}</b></td>
            <td>{c.start}{c.pre && <div className="note">早于数据起点，按上次烧焦推算</div>}</td><td>{c.done ? c.end : "至今（" + c.end.slice(0, 10) + "）"}</td>
            <td className="r">{c.days}</td><td>{c.why}</td><td>{c.done ? <span className="tag t-ok">完整</span> : <span className="tag t-warn">未结束</span>}</td></tr>))
          : <tr><td colSpan={6} className="note" style={{ textAlign: "center", padding: 16 }}>不划分周期，训练时按时间顺序划分</td></tr>}</tbody></table></div>
      <p className="note" style={{ margin: "8px 0 0" }}>{R.cycle_mode === "none" ? "" : full < 2 ? <span className="err">完整周期少于 2 个，无法按周期划分训练和检验数据。</span>
        : `共 ${full} 个完整周期，${cs.length - full} 个未结束。周期按最终工况中持续 6 小时以上的${R.cycle_mode === "burn" ? "烧焦" : "烧焦或停炉"}划分；"本周期运行天数"等累计量使用相同的边界（上线时按规则实时判定）。`}
</p>
    </div>
  );
}
