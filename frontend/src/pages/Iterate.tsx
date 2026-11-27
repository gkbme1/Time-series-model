import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";
import { api } from "../api";
import { EChart } from "../components/EChart";
import { go } from "../router";
import { cssVar, Spinner, useToast } from "../ui";

type Smp = { id: number; kind: string; t: string; t_end: string; reason: string; detail: string; status: string; cause: string; memo: string; instance_id: number; reviewer: string; n_pairs: number };
type IP = { samples: Smp[]; by_kind: Record<string, number>; pending: number; accepted: number; psi: { name: string; psi: number; n: number }[]; causes: string[]; clock: any };
const ST: Record<string, string> = { 待复核: "t-acc", 已入训练集: "t-ok", 已排除: "t-mute" };

export default function IteratePage({ sid }: { sid: number }) {
  const qc = useQueryClient();
  const [st, setSt] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const [adv, setAdv] = useState("");
  const q = useQuery({ queryKey: ["iterate", sid, st], queryFn: () => api.get<IP>(`/api/scenarios/${sid}/iterate?st=${encodeURIComponent(st)}`), refetchInterval: 5000, placeholderData: (p) => p });
  const I = q.data;
  if (!I) return <Spinner text="加载中…" />;
  const cur = I.samples.find((x) => x.id === sel) || I.samples[0];
  const advice = () => {
    const drift = I.psi.filter((p) => p.psi > 0.25).map((p) => p.name);
    const lines = [
      `近 30 天有 ${drift.length} 个输入显著漂移（PSI > 0.25）${drift.length ? "：" + drift.join("、") : ""}。`,
      `样本库共 ${I.samples.length} 条：待复核 ${I.pending} 条，已复核进入训练集 ${I.accepted} 条。`,
      I.accepted ? "建议：到\"选数据\"把时间范围延长到当前时刻，\"跟踪改进：复核后加入训练集的样本\"会作为一个校核来源出现；生成新的数据版本后重新训练，重训练结果需经独立检验和审批才能发布。"
        : "建议：先复核待分析样本，把校核值可信、工况有代表性的样本加入训练集，再生成新的数据版本重新训练。",
    ];
    setAdv(lines.join("\n"));
  };
  return (
    <div className="grid">
      <div className="c s7"><h2>待分析样本库 <span className="sub">在线运行中自动回收，人工复核后才能进入训练集</span></h2>
        <div className="row" style={{ marginBottom: 8 }}><select aria-label="按状态筛选" value={st} onChange={(e) => setSt(e.target.value)}><option value="">全部状态</option><option>待复核</option><option>已入训练集</option><option>已排除</option></select>
          <span className="note">{Object.entries(I.by_kind).map(([k, v]) => `${k} ${v}`).join(" · ") || "还没有触发的样本：开始回放并部署在线实例后自动回收"}</span></div>
        <div className="scroll"><table className="stack"><thead><tr><th>触发时间</th><th>实例</th><th>触发原因</th><th>状态</th></tr></thead>
          <tbody>{I.samples.length ? I.samples.map((x) => (
            <tr key={x.id} className={"click" + (cur?.id === x.id ? " sel" : "")} onClick={() => setSel(x.id)}><td style={{ whiteSpace: "nowrap" }}>{x.t}</td><td>#{x.instance_id}</td><td>{x.reason}</td><td><span className={"tag " + ST[x.status]}>{x.status}</span></td></tr>))
            : <tr><td colSpan={4} className="note" style={{ textAlign: "center", padding: 18 }}>没有样本</td></tr>}</tbody></table></div>
        <p className="note">触发条件：误差持续增大（连续 2 个以上校核点 &gt; 15 ℃）、输入超出适用范围 ≥ 30 min、识别出新工况 ≥ 30 min、输入数据质量异常 ≥ 30 min、新旧模型输出差异 &gt; 12 ℃ 且 ≥ 10 min，以及在场景概览中标记的较大偏差。</p></div>
      <div className="c s5">{cur ? <SampleDetail key={cur.id} x={cur} causes={I.causes} onDone={() => qc.invalidateQueries({ queryKey: ["iterate", sid] })} /> : <p className="note">选择左侧的样本查看详情</p>}</div>
      <div className="c s12"><h2>输入分布漂移 <span className="sub">PSI，在线实例近 30 天输入 vs 训练数据；&gt; 0.25 为显著漂移，0.1–0.25 需关注</span></h2>
        {I.psi.length ? I.psi.map((p) => <div className="rule" key={p.name}><span style={{ width: 140 }}>{p.name}</span><div className="bar" style={{ flex: 1 }}><b style={{ width: `${Math.min(100, (p.psi / 1) * 100)}%`, background: p.psi > 0.25 ? "var(--bad)" : p.psi > 0.1 ? "var(--warn)" : "var(--teal)" }} /></div>
          <span className="note" style={{ width: 52, textAlign: "right" }}>{p.psi.toFixed(2)}</span></div>) : <p className="note">还没有在线实例的运行数据。</p>}
        <div className="row" style={{ marginTop: 10 }}><button onClick={advice}>生成重训练建议</button><span className="note">重训练结果需经独立检验和审批才能发布</span><a className="link" style={{ marginLeft: "auto" }} onClick={() => go(`s${sid}-data`)}>去选数据</a></div>
        {adv && <div className="lagHint ok" style={{ marginTop: 10, whiteSpace: "pre-line" }}>{adv}</div>}</div>
    </div>
  );
}

function SampleDetail({ x, causes, onDone }: { x: Smp; causes: string[]; onDone: () => void }) {
  const toast = useToast();
  const dq = useQuery({ queryKey: ["sample", x.id], queryFn: () => api.get<Smp & { chart: { t: string[]; v: (number | null)[]; points: [string, number, string][]; mark: [string, string] } }>(`/api/samples/${x.id}`) });
  const [cause, setCause] = useState(x.cause || causes[0]);
  const [use, setUse] = useState<string>(x.status === "已入训练集" ? "1" : x.status === "已排除" ? "0" : "");
  const [memo, setMemo] = useState(x.memo);
  const [err, setErr] = useState("");
  const ch = dq.data?.chart;
  const opt = useMemo<EChartsOption | null>(() => {
    if (!ch) return null;
    const mute = cssVar("--mute"), ink = cssVar("--ink");
    return { animation: false, grid: { left: 48, right: 10, top: 10, bottom: 24 }, tooltip: { trigger: "axis" }, xAxis: { type: "time", axisLabel: { color: mute } }, yAxis: { type: "value", scale: true, axisLabel: { color: mute } },
      series: [{ type: "line", data: ch.t.map((t, i) => [t, ch.v[i]]), symbol: "none", lineStyle: { color: "#d9480f" }, markArea: { silent: true, data: [[{ xAxis: ch.mark[0], itemStyle: { color: "#e67700", opacity: 0.15 } }, { xAxis: ch.mark[1] }]] as any } },
        { type: "scatter", data: ch.points.map((p) => [p[0], p[1]]), symbolSize: 6, itemStyle: { color: ink } }] };
  }, [ch]);
  const save = async () => {
    if (!use) { setErr("请选择是否允许进入训练集"); return; }
    await api.post(`/api/samples/${x.id}/review`, { use: use === "1", cause, memo });
    toast("复核已提交"); onDone();
  };
  return (<>
    <h2>样本详情 <span className="sub">{x.t} · 实例 #{x.instance_id}</span></h2>
    <p style={{ margin: "0 0 10px" }}>{x.detail}</p>
    {opt && ch && ch.t.length ? <EChart option={opt} height={180} /> : <p className="note">该时段没有输出数据</p>}
    <p className="note">橙色为触发时段，前后各取 2 小时：橙线为模型输出（空缺处未输出），黑点为校核值。</p>
    <div className="form" style={{ marginTop: 12 }}><span className="lab">原因判定</span><select value={cause} onChange={(e) => setCause(e.target.value)}>{causes.map((c) => <option key={c}>{c}</option>)}</select>
      <span className="lab">进入训练集</span><div className="radios"><label><input type="radio" checked={use === "1"} onChange={() => { setUse("1"); setErr(""); }} /> 允许</label><label><input type="radio" checked={use === "0"} onChange={() => { setUse("0"); setErr(""); }} /> 不允许</label></div>
      <span className="lab">备注</span><textarea placeholder="分析说明" value={memo} onChange={(e) => setMemo(e.target.value)} /></div>
    <p className="note err">{err}</p>
    <div className="row"><button className="pri" onClick={save}>提交复核</button>{x.reviewer && <span className="note">上次复核：{x.reviewer} · {x.status}{x.n_pairs ? ` · ${x.n_pairs} 个校核值进入训练来源` : ""}</span>}</div>
  </>);
}
