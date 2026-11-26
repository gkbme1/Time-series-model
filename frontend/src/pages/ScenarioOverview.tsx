import { useQuery } from "@tanstack/react-query";
import { api, ScenarioLite } from "../api";
import { FNS } from "../router";
import { tgLabel } from "../App";
import { Spinner } from "../ui";

type Ov = { model: { code: string; published_at: string; mae: number | null; furnace: string } | null; stages: { name: string; state: string; info: string }[]; definition: [string, string][]; assets: [string, string][] };

export default function ScenarioOverview({ sc }: { sc: ScenarioLite }) {
  const ov = useQuery({ queryKey: ["overview", sc.id], queryFn: () => api.get<Ov>(`/api/scenarios/${sc.id}/overview`) });
  const links = FNS.slice(1).map((f) => f[0]);
  const kpi = (l: string, d: string) => (
    <div className="c kpi s3"><div className="l">{l}</div><div className="v na">—</div><div className="d">{d}</div></div>
  );
  return (
    <div className="grid">
      {ov.data?.model ? <div className="c kpi s3"><div className="l">当前在线模型</div><div className="v">{ov.data.model.code}</div><div className="d">{ov.data.model.published_at} 发布 · 检验偏差 {ov.data.model.mae} ℃ · 待部署</div></div> : kpi("当前在线模型", "尚未发布模型")}
      {kpi("平均预测偏差（近 7 天）", "上线投用并有校核值后计算")}
      {kpi("适用范围内占比", "上线投用后计算")}
      {kpi("较大偏差次数（近 7 天）", "上线投用后计算")}
      <div className="c s12"><h2>建模流程 <span className="sub">各阶段可独立回退和重复执行</span></h2>
        {!ov.data ? <Spinner /> : <div className="pipe">{ov.data.stages.map((s, i) => (
          <a key={s.name} href={`#s${sc.id}-${links[i]}`} className={s.state === "done" ? "done" : s.state === "cur" ? "cur" : ""}><span className="note">{i + 1}</span><b>{s.name}</b><span className="note">{s.info}</span></a>))}</div>}
      </div>
      <div className="c s7"><h2>场景定义</h2><table><tbody>
        <tr><td>估算目标</td><td className="r">{tgLabel(sc.target)}</td></tr>
        {ov.data?.definition.map((a) => <tr key={a[0]}><td>{a[0]}</td><td className="r">{a[1]}</td></tr>)}</tbody></table></div>
      <div className="c s5"><h2>场景资产</h2><table><tbody>{ov.data?.assets.map((a) => <tr key={a[0]}><td>{a[0]}</td><td className="r">{a[1]}</td></tr>)}</tbody></table></div>
    </div>
  );
}
