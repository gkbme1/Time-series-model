import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, Template } from "../api";
import { Spinner } from "../ui";

export function TemplateCards({ list, sel, onSel }: { list: Template[]; sel: string; onSel: (id: string) => void }) {
  return (
    <div className="tpls">{list.map((x) => (
      <button key={x.id} className={"tpl" + (x.id === sel ? " on" : "")} onClick={() => onSel(x.id)}>
        <h3>{x.name}</h3><p>{x.desc}</p>
        <div className="meta">{x.tags.map((g) => <span key={g} className="tag t-mute">{g}</span>)}{x.incomplete && <span className="tag t-warn">Demo 仅内容示意</span>}</div>
      </button>))}</div>
  );
}

export default function Templates() {
  const q = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/api/templates") });
  const [tl, setTl] = useState("cracking_tmt");
  if (!q.data) return <Spinner text="加载中…" />;
  const t = q.data.find((x) => x.id === tl) || q.data[0];
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}><button className="pri" disabled title="Demo 版本暂不支持">从当前项目另存为模板</button><button disabled title="Demo 版本暂不支持">导入模板</button><span className="note">模板 = 装置类型 × 建模场景，只包含配置与知识，不包含代码；文件位于 data/templates/</span></div>
      <TemplateCards list={q.data} sel={tl} onSel={setTl} />
      <div className="c" style={{ marginTop: 14 }}>
        <h2>{t.name} <span className="sub">模板内容{t.version ? ` · v${t.version}` : ""}</span></h2>
        <table className="stack"><thead><tr><th style={{ width: 140 }}>配置项</th><th>内容</th></tr></thead>
          <tbody>{t.detail.map((r) => <tr key={r[0]}><td><b>{r[0]}</b></td><td>{r[1]}</td></tr>)}</tbody></table>
      </div>
    </>
  );
}
