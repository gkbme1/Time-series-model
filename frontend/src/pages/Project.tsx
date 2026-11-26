import { useQuery } from "@tanstack/react-query";
import { api, Project, Tag } from "../api";
import { go } from "../router";
import { statusClass } from "../App";

export default function ProjectPage({ project }: { project: Project }) {
  const furn = useQuery({ queryKey: ["furnaces"], queryFn: () => api.get<{ unit: string }[]>("/api/furnaces") });
  const scs = project.scenarios;
  return (
    <div className="grid">
      <div className="c kpi s3"><div className="l">场景数</div><div className="v">{scs.length}</div><div className="d">{scs.filter((s) => s.type === "软测量").length} 个软测量 · {scs.filter((s) => s.type === "预测").length} 个预测</div></div>
      <div className="c kpi s3"><div className="l">在线运行实例</div><div className="v">0</div><div className="d">尚未上线（阶段 4 实现上线投用）</div></div>
      <div className="c kpi s3"><div className="l">待分析样本（合计）</div><div className="v">0</div><div className="d">上线后自动回收</div></div>
      <div className="c kpi s3"><div className="l">数据源</div><div className="v" style={{ fontSize: 20 }}>本地文件</div><div className="d">{furn.data?.length ?? "…"} 台炉的历史数据 · 接口预留 XBase</div></div>
      <div className="c s12"><h2>场景列表 <span className="sub">点击进入场景</span><button className="pri" style={{ marginLeft: "auto" }} onClick={() => go("newscen")}>新建场景</button></h2>
        <div className="scroll"><table className="stack"><thead><tr><th>场景</th><th>类型</th><th>场景模板</th><th>设备</th><th>当前阶段</th><th>在线模型</th><th>状态</th></tr></thead>
          <tbody>{scs.length ? scs.map((s) => (
            <tr key={s.id} className="click" onClick={() => go(`s${s.id}-sov`)}><td><b>{s.name}</b></td><td>{s.type}</td><td>{s.template_name}</td><td>{s.furnace}</td><td>{s.stage}</td><td>{s.model}</td><td><span className={"tag " + statusClass(s.status)}>{s.status}</span></td></tr>
          )) : <tr><td colSpan={7} className="note" style={{ textAlign: "center", padding: 28 }}>还没有场景，点击右上角"新建场景"</td></tr>}</tbody></table></div></div>
      <div className="c s6"><h2>项目公共配置 <span className="sub">各场景共用</span></h2><table><tbody>
        <tr><td>所属装置</td><td className="r">{project.device}</td></tr>
        <tr><td>数据源</td><td className="r">本地文件（data/timeseries）</td></tr>
        <tr><td>本体模型</td><td className="r">{project.ontology}（data/tags.csv）</td></tr>
        <tr><td>结果发布</td><td className="r">本地输出表</td></tr></tbody></table></div>
      <div className="c s6"><h2>项目成员 <span className="sub">Demo 不做登录，固定名称</span></h2><table><tbody>
        <tr><td>王工</td><td>项目负责人</td><td className="r">全部权限</td></tr><tr><td>李工</td><td>工艺工程师</td><td className="r">工况标注、样本复核、发布审批</td></tr>
        <tr><td>赵工</td><td>建模工程师</td><td className="r">数据、特征、训练、验证</td></tr><tr><td>运维组</td><td>运维</td><td className="r">部署运行、服务状态</td></tr></tbody></table></div>
    </div>
  );
}
