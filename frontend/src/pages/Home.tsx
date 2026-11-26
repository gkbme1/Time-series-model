import { Project } from "../api";

export default function Home({ projects, onOpen, onNew, onOpenDlg }: { projects: Project[]; onOpen: (id: number) => void; onNew: () => void; onOpenDlg: () => void }) {
  return (
    <div className="c empty">
      <h2>当前没有打开的项目</h2>
      <p>打开已有项目，或新建一个项目后在其中添加预测和软测量场景。</p>
      <div className="row" style={{ justifyContent: "center", marginTop: 12 }}><button className="pri" onClick={onOpenDlg}>打开项目</button><button onClick={onNew}>新建项目</button></div>
      <div style={{ maxWidth: 560, margin: "28px auto 0", textAlign: "left" }}>
        <h2>最近打开</h2>
        {projects.map((p) => (
          <div className="rule" key={p.id}><b>{p.name}</b><span className="note">{p.device} · {p.scenarios.length} 个场景</span><button className="x" onClick={() => onOpen(p.id)}>打开</button></div>
        ))}
      </div>
    </div>
  );
}
