import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { EChart } from "../components/EChart";
import { go } from "../router";
import { cssVar, Modal, Spinner, useToast } from "../ui";

export type ClockS = { sim: string; speed: number; running: boolean; end: string; speeds: number[] };
type Inst = { id: number; furnace: string; furnace_name: string; model: string; mode: string; period: number; created: string; output_tag: string; last: any; cross: any; last_t: string };
type DP = { clock: ClockS; instances: Inst[]; published: { id: number; code: string; mae: number }[]; furnaces: { unit: string; name: string }[]; home: string };
type Live = { clock: ClockS; instance: Inst; target: any; last: any; trend: [string, number | null, number | null, number | null][]; ref: [string, number | null][]; points: [string, number, string][];
  inputs: { name: string; unit: string; value: number | null; p1: number; p99: number; lo: number; hi: number; hmin: number; hmax: number; state: string }[]; n_2h: number; n_bad_2h: number };

const MODE: Record<string, [string, string]> = { online: ["在线", "t-ok"], shadow: ["影子测试", "t-teal"], stopped: ["已停用", "t-mute"] };
const CAT: Record<string, string> = { quality: "输入质量异常", range: "超出范围", regime: "没学过的工况", mahal: "组合异常", rule: "人工规则" };
const dot = (c: string) => <span className="dot" style={{ background: c }} />;

export function ClockBar({ compact }: { compact?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const cq = useQuery({ queryKey: ["clock"], queryFn: () => api.get<ClockS>("/api/clock"), refetchInterval: 1000 });
  const C = cq.data;
  const [jump, setJump] = useState("");
  if (!C) return null;
  const put = async (b: object) => { try { qc.setQueryData(["clock"], await api.put<ClockS>("/api/clock", b)); qc.invalidateQueries(); } catch (e) { toast((e as Error).message); } };
  return (
    <div className="row" style={{ gap: 10 }}>
      <span className="note">回放时钟</span><b style={{ fontSize: compact ? 14 : 18, fontVariantNumeric: "tabular-nums" }}>{C.sim.slice(0, 16)}</b>
      <span className={"tag " + (C.running ? "t-ok" : "t-mute")}>{C.running ? `运行中 · ${C.speed} 倍` : C.sim.slice(0, 16) >= C.end ? "已到数据末尾" : "已暂停"}</span>
      {!compact && <>
        <button className={C.running ? "" : "pri"} onClick={() => put({ running: !C.running })}>{C.running ? "暂停" : "开始回放"}</button>
        <select aria-label="倍速" value={C.speed} onChange={(e) => put({ speed: +e.target.value })}>{C.speeds.map((s) => <option key={s} value={s}>{s} 倍{s === 60 ? "（1 秒 = 1 分钟）" : s === 600 ? "（1 秒 = 10 分钟）" : "（1 秒 = 1 小时）"}</option>)}</select>
        <input type="datetime-local" className="din" value={jump} onChange={(e) => setJump(e.target.value)} aria-label="跳到" />
        <button disabled={!jump} onClick={() => { put({ sim: jump.replace("T", " ") }); toast(`回放时钟已跳到 ${jump.replace("T", " ")}`); }}>跳到</button>
        <span className="note">数据到 {C.end}；时钟只决定"现在"是什么时刻，上线计算读不到这之后的数据</span></>}
    </div>
  );
}

export default function DeployPage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const dq = useQuery({ queryKey: ["deploy", sid], queryFn: () => api.get<DP>(`/api/scenarios/${sid}/deploy`), refetchInterval: 2000 });
  const D = dq.data;
  const [sel, setSel] = useState<number | null>(null);
  const [wiz, setWiz] = useState(false);
  const inst = D?.instances.find((i) => i.id === sel) || D?.instances.find((i) => i.mode === "online") || D?.instances[0];
  const lq = useQuery({ queryKey: ["live", inst?.id], queryFn: () => api.get<Live>(`/api/instances/${inst!.id}/live`), enabled: !!inst, refetchInterval: D?.clock.running ? 1000 : 3000 });
  const L = lq.data;
  const setMode = async (i: Inst, mode: string) => {
    try { await api.put(`/api/instances/${i.id}/mode`, { mode }); toast(mode === "online" ? `${i.furnace_name} · ${i.model} 已切换为在线` : mode === "stopped" ? "已停用" : "已改为影子模式"); qc.invalidateQueries(); }
    catch (e) { toast((e as Error).message); }
  };
  if (!D) return <Spinner text="加载中…" />;
  return (
    <div className="grid">
      <div className="c s12" style={{ padding: "10px 16px" }}><ClockBar /></div>
      <div className="c s12"><h2>运行实例 <span className="sub">同一模型可部署到多台炉，测点映射和周期独立配置；新版本先以影子模式与在线版本并行</span>
        <button className="pri" style={{ marginLeft: "auto" }} disabled={!D.published.length} onClick={() => setWiz(true)}>新增运行实例</button></h2>
        {!D.published.length ? <p className="note">还没有已发布的模型。请先在"检验效果"中审批发布。<a className="link" onClick={() => go(`s${sid}-validate`)}>去检验效果</a></p> :
          <div className="scroll"><table className="stack"><thead><tr><th>实例</th><th>模型版本</th><th>模式</th><th className="r">周期</th><th>输入质量</th><th>适用性</th><th className="r">最新值</th><th>部署于</th><th></th></tr></thead>
            <tbody>{D.instances.length ? D.instances.map((i) => { const l = i.last || {}; return (
              <tr key={i.id} className={"click" + (inst?.id === i.id ? " sel" : "")} onClick={() => setSel(i.id)}>
                <td><b>{i.furnace_name}</b><div className="note">输出位号 {i.output_tag || "—"}</div></td><td>{i.model}</td><td><span className={"tag " + MODE[i.mode][1]}>{MODE[i.mode][0]}</span></td><td className="r">{i.period} s</td>
                <td>{l.quality ? (l.quality === "正常" ? <span className="tag t-ok">正常</span> : <span className="tag t-bad" title={l.quality}>{l.quality.slice(0, 14)}</span>) : "—"}</td>
                <td>{l.t ? (l.applicable ? <span className="tag t-ok">范围内</span> : <span className="tag t-warn" title={l.reason}>不适用 · {CAT[l.reason_cat] || l.reason_cat}</span>) : "—"}</td>
                <td className="r">{l.t ? (l.value == null ? <span className="note">不输出</span> : `${l.value.toFixed(1)} ℃`) : "—"}</td><td className="note">{i.created}</td>
                <td onClick={(e) => e.stopPropagation()}><div className="row" style={{ flexWrap: "nowrap" }}>
                  {i.mode !== "online" && i.mode !== "stopped" && <button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => setMode(i, "online")}>设为在线</button>}
                  {i.mode !== "stopped" && <button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => setMode(i, "stopped")}>停用</button>}
                  {i.mode === "stopped" && <button style={{ minHeight: 30, padding: "2px 10px" }} onClick={() => setMode(i, "shadow")}>以影子模式恢复</button>}</div></td></tr>); })
              : <tr><td colSpan={9} className="note" style={{ textAlign: "center", padding: 20 }}>还没有运行实例，点右上角"新增运行实例"把 {D.published.map((p) => p.code).join("、")} 部署到炉子上</td></tr>}</tbody></table></div>}</div>

      {inst && L && <LiveCards L={L} />}
      {wiz && <NewInstance sid={sid} D={D} onClose={() => setWiz(false)} onDone={(id) => { setWiz(false); setSel(id); qc.invalidateQueries(); }} />}
    </div>
  );
}

function LiveCards({ L }: { L: Live }) {
  const l = L.last, i = L.instance;
  const opt = useMemo<EChartsOption>(() => {
    const ink = cssVar("--ink"), mute = cssVar("--mute");
    return { animation: false, grid: { left: 50, right: 12, top: 12, bottom: 26 }, tooltip: { trigger: "axis", valueFormatter: (v: any) => (v == null ? "不输出" : (+v).toFixed(1)) },
      xAxis: { type: "time", axisLabel: { color: mute } }, yAxis: { type: "value", scale: true, axisLabel: { color: mute } },
      series: [
        { type: "line", data: L.trend.map((r) => [r[0], r[2]]), stack: "b", lineStyle: { opacity: 0 }, symbol: "none", silent: true, tooltip: { show: false } },
        { type: "line", data: L.trend.map((r) => [r[0], r[2] == null || r[3] == null ? null : r[3] - r[2]]), stack: "b", lineStyle: { opacity: 0 }, symbol: "none", areaStyle: { color: "#d9480f", opacity: 0.16 }, silent: true, tooltip: { show: false } },
        { type: "line", name: "软测量值", data: L.trend.map((r) => [r[0], r[1]]), symbol: "none", lineStyle: { color: "#d9480f", width: 2 } },
        { type: "line", name: "1# 管热电偶（扣除偏差，参考）", data: L.ref, symbol: "none", lineStyle: { color: mute, width: 1, type: "dashed" } },
        { type: "scatter", name: "校核值", data: L.points.map((p) => [p[0], p[1]]), symbolSize: 7, itemStyle: { color: ink } },
      ] };
  }, [L]);
  const light = (ok: boolean | null, txt: string) => <b>{dot(ok == null ? "var(--mute)" : ok ? "var(--ok)" : "var(--warn)")}{txt}</b>;
  return (<>
    <div className="c s4"><h2>{i.furnace_name} · {L.target.name}</h2>
      {!l ? <p className="note">等待第一次运行（开始回放后每 {i.period} 秒模拟时间运行一次）</p> : <>
        <div className="live">{l.value == null ? <span className="big" style={{ color: "var(--mute)", fontSize: 34 }}>不输出</span> : <><span className="big">{l.value.toFixed(0)}</span><span>{L.target.unit}</span><span className="note">区间 {l.p10.toFixed(0)}–{l.p90.toFixed(0)}</span></>}</div>
        <div className="note">更新于 {l.t} · 模型 {i.model} · {MODE[i.mode][0]}{l.value == null ? ` · ${l.reason}` : ""}</div>
        <div className="lights"><div>输入质量{light(l.quality === "正常", l.quality === "正常" ? "正常" : l.quality.replace("异常：", "异常："))}</div>
          <div>适用性{light(l.applicable, l.applicable ? "范围内" : CAT[l.reason_cat] || "不适用")}</div>
          <div>当前工况<b>{l.regime_name}</b></div><div>服务状态{light(true, `正常 · ${l.latency_ms} ms`)}</div></div>
        <p className="note">结果写入本地输出表{i.output_tag ? `（位号 ${i.output_tag}）` : ""}，{i.mode === "shadow" ? "影子模式：只记录、不作为对外输出；" : ""}仅用于监视参考，不进入控制回路。近 2 小时 {L.n_2h} 次运行，其中 {L.n_bad_2h} 次不适用未输出。</p></>}</div>
    <div className="c s8"><h2>实时趋势 <span className="sub">最近 2 小时（模拟时间），随回放时钟刷新；空缺处为不适用，未输出</span></h2><EChart option={opt} height={280} />
      <div className="legend"><span><i style={{ background: "#d9480f" }} />软测量值</span><span><i style={{ background: "#d9480f", opacity: 0.25, height: 8 }} />P10–P90</span><span><i style={{ background: "var(--mute)" }} />1# 管热电偶（扣除偏差，参考）</span><span><i style={{ background: "var(--ink)", width: 8, height: 8, borderRadius: "50%" }} />校核值（巡检红外 / 热电偶 / 手工）</span></div></div>
    <div className="c s12"><h2>输入变量与训练范围 <span className="sub">浅色条为训练数据 P1–P99，橙色线为生效范围，竖线为当前值</span></h2>
      <div className="scroll"><table className="stack"><thead><tr><th>变量</th><th className="r">当前值</th><th style={{ width: "44%" }}>位置</th><th>状态</th></tr></thead>
        <tbody>{L.inputs.map((r) => { const p = (v: number) => Math.max(0, Math.min(100, ((v - r.hmin) / (r.hmax - r.hmin)) * 100)); return (
          <tr key={r.name}><td>{r.name}</td><td className="r">{r.value == null ? "—" : `${Math.abs(r.value) >= 100 ? r.value.toFixed(0) : r.value.toFixed(2)} ${r.unit}`}</td>
            <td><div className="range"><div className="tr" style={{ left: `${p(r.p1)}%`, width: `${p(r.p99) - p(r.p1)}%` }} />
              {[r.lo, r.hi].map((x, k) => <div key={k} style={{ position: "absolute", top: -2, bottom: -2, width: 2, background: "#d9480f", left: `${p(x)}%` }} />)}
              {r.value != null && <div className="pt" style={{ left: `${p(r.value)}%`, background: r.state === "范围内" ? "var(--ink)" : "var(--bad)" }} />}</div>
              <div className="row note" style={{ justifyContent: "space-between" }}><span>{r.hmin}</span><span>{r.hmax}</span></div></td>
            <td><span className={"tag " + (r.state === "范围内" ? "t-ok" : r.state === "超出" ? "t-warn" : "t-bad")}>{r.state}</span></td></tr>); })}</tbody></table></div></div>
  </>);
}

function NewInstance({ sid, D, onClose, onDone }: { sid: number; D: DP; onClose: () => void; onDone: (id: number) => void }) {
  const toast = useToast();
  const [cand, setCand] = useState(D.published[0].id);
  const used = (f: string) => D.instances.some((i) => i.furnace === f && i.model === D.published.find((p) => p.id === cand)?.code && i.mode !== "stopped");
  const [fur, setFur] = useState(D.furnaces.find((f) => !D.instances.some((i) => i.furnace === f.unit && i.mode !== "stopped"))?.unit || D.home);
  const [map, setMap] = useState<any>(null);
  const [cross, setCross] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("online");
  const [err, setErr] = useState("");
  const home = fur === D.home;
  const hasOnline = D.instances.some((i) => i.furnace === fur && i.mode === "online");
  useEffect(() => { setMap(null); setCross(null); setErr(""); api.post(`/api/scenarios/${sid}/deploy/prepare`, { cand_id: cand, furnace: fur }).then(setMap).catch((e) => setErr(e.message)); }, [cand, fur]);
  useEffect(() => setMode(home && !hasOnline ? "online" : "shadow"), [fur]);
  const runCross = async () => { setBusy(true); setErr(""); try { setCross(await api.post(`/api/scenarios/${sid}/deploy/cross`, { cand_id: cand, furnace: fur })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const create = async () => {
    try { const r = await api.post<{ id: number }>(`/api/scenarios/${sid}/deploy/create`, { cand_id: cand, furnace: fur, mode: home ? mode : "shadow", cross }); toast(`${D.furnaces.find((f) => f.unit === fur)?.name} 实例已创建（${home && mode === "online" ? "在线" : "影子模式"}），开始回放后每分钟运行一次`); onDone(r.id); }
    catch (e) { setErr((e as Error).message); }
  };
  const mapOk = map && map.n_ok === map.n;
  const canCreate = mapOk && (home || (cross && cross.ok)) && !used(fur);
  return (
    <Modal open onClose={onClose} wide>
      <h2>新增运行实例<button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <div className="form" style={{ gridTemplateColumns: "84px minmax(0,1fr)" }}>
        <span className="lab">模型</span><select value={cand} onChange={(e) => setCand(+e.target.value)}>{D.published.map((p) => <option key={p.id} value={p.id}>{p.code}（检验偏差 {p.mae} ℃）</option>)}</select>
        <span className="lab">部署到</span><select value={fur} onChange={(e) => setFur(e.target.value)}>{D.furnaces.map((f) => <option key={f.unit} value={f.unit}>{f.name}{f.unit === D.home ? "（模型训练所用的炉）" : ""}</option>)}</select></div>
      <h3>① 测点映射 <span className="note" style={{ fontWeight: 400 }}>按参数名把模型用到的参数对应到这台炉的位号</span></h3>
      {!map ? <Spinner text="映射中…" /> : <><p style={{ margin: "0 0 6px" }}>{map.n_ok} / {map.n} 个参数已自动映射{mapOk ? <span className="tag t-ok" style={{ marginLeft: 6 }}>全部完成</span> : <span className="tag t-warn" style={{ marginLeft: 6 }}>{map.n - map.n_ok} 个需人工指定</span>}</p>
        <details><summary className="note" style={{ cursor: "pointer" }}>查看映射表</summary><table><thead><tr><th>参数</th><th>模型位号</th><th>本炉位号</th><th>用途</th></tr></thead>
          <tbody>{map.rows.map((r: any) => <tr key={r.name}><td>{r.name}</td><td>{r.src}</td><td>{r.dst || <span className="err">找不到</span>}</td><td className="note">{r.input ? "模型输入" : "工况判定 / 组合量"}</td></tr>)}</tbody></table></details></>}
      <h3>② 跨炉检验 <span className="note" style={{ fontWeight: 400 }}>{home ? "模型就是用这台炉的数据训练和检验的，不需要跨炉检验" : "用这台炉最近 3 个月的数据回放一次（同一条计算链 + 这台炉的巡检红外校核值）"}</span></h3>
      {home ? <div className="row"><span className="note">运行模式</span><label className="row" style={{ gap: 4 }}><input type="radio" checked={mode === "online"} disabled={hasOnline} onChange={() => setMode("online")} />在线{hasOnline ? "（已有在线实例）" : ""}</label>
        <label className="row" style={{ gap: 4 }}><input type="radio" checked={mode === "shadow"} onChange={() => setMode("shadow")} />影子测试（只记录，与在线版本并行比较）</label></div>
        : cross ? <><div className="mk" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))" }}><div>本炉检验偏差<b>{cross.home_mae} ℃</b></div><div>{D.furnaces.find((f) => f.unit === fur)?.name}<b style={{ color: cross.ok ? "var(--ink)" : "var(--bad)" }}>{cross.mae ?? "—"} ℃</b></div>
          <div>系统偏差<b>{cross.bias == null ? "—" : (cross.bias > 0 ? "+" : "") + cross.bias + " ℃"}</b></div><div>校核点<b>{cross.n_out} / {cross.n}</b></div></div>
          <p className="note" style={{ margin: 0 }}>{cross.period?.join(" 至 ")} · 可接受上限 {cross.limit} ℃（本炉偏差的 1.5 倍或 +3 ℃，取大者）。{cross.msg}</p></>
          : <button disabled={!mapOk || busy} onClick={runCross}>{busy ? "检验中…" : "开始检验"}</button>}
      {!home && <p className="note">跨炉部署一律先以影子模式运行，对比一段时间后再切换为在线。</p>}
      {used(fur) && <p className="note err">这个模型已部署到这台炉。</p>}
      <p className="note err">{err}</p>
      <div className="row" style={{ marginTop: 14 }}><button style={{ marginLeft: "auto" }} onClick={onClose}>取消</button><button className="pri" disabled={!canCreate} onClick={create}>{home && mode === "online" ? "创建实例（在线）" : "创建实例（影子模式）"}</button></div>
    </Modal>
  );
}
