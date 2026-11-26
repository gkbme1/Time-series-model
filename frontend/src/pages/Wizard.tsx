import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, Furnace, Project, Scenario, Template } from "../api";
import { go } from "../router";
import { Spinner, useToast } from "../ui";
import { TemplateCards } from "./Templates";

const NAMES = ["选择场景模板", "选择设备", "定义目标与校核数据", "确认创建"];

export default function Wizard({ project }: { project: Project }) {
  const qc = useQueryClient();
  const toast = useToast();
  const tq = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/api/templates") });
  const fq = useQuery({ queryKey: ["furnaces"], queryFn: () => api.get<Furnace[]>("/api/furnaces") });
  const [wz, setWz] = useState(0);
  const [tsel, setTsel] = useState("cracking_tmt");
  const [fur, setFur] = useState("BA-101");
  const [name, setName] = useState("");
  const [ty, setTy] = useState("软测量");
  const [err, setErr] = useState("");
  if (!tq.data || !fq.data) return <Spinner text="加载中…" />;
  const t = tq.data.find((x) => x.id === tsel)!;
  const f = fq.data.find((x) => x.unit === fur)!;
  const k = fur.split("-")[1].slice(-1);
  const defName = `${k}号炉 ${t.name.split(" · ")[1] || t.name}${t.id === "fc" ? "" : "软测量"}`;
  const next = async () => {
    if (wz === 1 && !fur) { setErr("请选择设备"); return; }
    if (wz < 3) { if (wz === 0) setTy(tsel === "fc" ? "预测" : "软测量"); setWz(wz + 1); setErr(""); return; }
    try {
      const sc = await api.post<Scenario>(`/api/projects/${project.id}/scenarios`, { name: name || defName, template_id: tsel, furnace: fur, type: ty });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      toast("场景已加入项目，开始选数据");
      go(`s${sc.id}-data`);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <div className="steps">{NAMES.map((n, i) => <span key={n} className={i === wz ? "on" : i < wz ? "ok" : ""}>{i + 1}. {n}</span>)}</div>
      <div className="c">
        {wz === 0 && <><h2>选择场景模板 <span className="sub">模板预置变量角色、处理规则、工况规则、加工方式和检验方式，创建后都可修改</span></h2><TemplateCards list={tq.data} sel={tsel} onSel={setTsel} />
          {t.incomplete && <p className="note" style={{ marginTop: 10 }}>Demo 版本只有"裂解炉 · 炉管壁温"模板内容完整；选择其他模板时只带入目标定义，其余需要手动配置。</p>}</>}
        {wz === 1 && <><h2>选择设备 <span className="sub">来自参数表（代替本体），模板匹配的设备类型：{t.device_type}</span></h2>
          <div className="tree"><b>乙烯装置</b><div>裂解区<div>{fq.data.map((x) => (
            <div key={x.unit} style={{ paddingLeft: 0 }}><label><input type="radio" name="dev" checked={fur === x.unit} onChange={() => setFur(x.unit)} /> {x.name}</label> <span className="note">历史数据 {x.from} 至 {x.to}</span></div>))}</div></div></div>
          <p className="note">选定后，平台按参数表中"设备—参数—测点"关系匹配位号，下一步在"选数据"中确认。其他炉暂无数据文件，未列出。</p></>}
        {wz === 2 && <><h2>定义目标与校核数据来源</h2><div className="form">
          <span className="lab">场景名称</span><input type="text" value={name || defName} onChange={(e) => setName(e.target.value)} />
          <span className="lab">建模类型</span><div className="radios"><label><input type="radio" checked={ty === "软测量"} onChange={() => setTy("软测量")} /> 软测量</label><label><input type="radio" checked={ty === "预测"} onChange={() => setTy("预测")} /> 时序预测</label></div>
          <span className="lab">目标变量</span><select><option>炉管壁温 TMT（出口段平均）</option></select>
          <span className="lab">校核数据来源</span><select><option>{t.calib_label}</option></select>
          <span className="lab">数据集划分</span><span>{t.split}（模板默认）</span></div>
          <p className="note" style={{ marginTop: 10 }}>目标的单位、合理范围、校核值取法和输出位号在"选数据"页顶部的"估算目标"中设置。</p></>}
        {wz === 3 && <><h2>确认创建</h2><table><tbody>
          <tr><td>场景名称</td><td>{name || defName}</td></tr><tr><td>场景模板</td><td>{t.name}</td></tr><tr><td>设备</td><td>{f.name}</td></tr>
          <tr><td>将导入</td><td>{t.id === "cracking_tmt" ? "推荐参数 8 个 · 每个参数的默认处理规则 · 工况规则 4 条 · 加工方式与组合量 · 响应方向期望" : "目标定义（其余需手动配置）"}</td></tr>
          <tr><td>校核数据来源</td><td>{t.calib_label}</td></tr></tbody></table></>}
      </div>
      <p className="note err">{err}</p>
      <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}><button disabled={wz === 0} onClick={() => setWz(wz - 1)}>上一步</button><button className="pri" onClick={next}>{wz === 3 ? "创建场景" : "下一步"}</button></div>
    </>
  );
}
