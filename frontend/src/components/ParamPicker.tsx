import { useEffect, useMemo, useRef, useState } from "react";
import { Tag } from "../api";
import { Modal } from "../ui";

type Node = { key: string; name: string; items: Tag[]; sub: Node[] };

// 参数选择弹窗：按"设备 → 部件 → 参数（参数名 + 位号 + 测点描述）"浏览
function buildTree(tags: Tag[]): Node[] {
  const out: Node[] = [];
  for (const t of tags) {
    let d = out.find((x) => x.name === t.device);
    if (!d) out.push((d = { key: t.device, name: t.device, items: [], sub: [] }));
    if (t.part) {
      let g = d.sub.find((x) => x.name === t.part);
      if (!g) d.sub.push((g = { key: t.device + "/" + t.part, name: t.part, items: [], sub: [] }));
      g.items.push(t);
    } else d.items.push(t);
  }
  return out;
}

export default function ParamPicker({ open, tags, selected, onClose, onOk }: { open: boolean; tags: Tag[]; selected: string[]; onClose: () => void; onOk: (s: string[]) => void }) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [rec, setRec] = useState(false);
  const [openK, setOpenK] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(tags), [tags]);
  useEffect(() => {
    if (open) { setSel(new Set(selected)); setQ(""); setRec(false); setOpenK(new Set(tree[0] ? [tree[0].key] : [])); }
  }, [open]);
  const hit = (x: Tag) => (!q || x.name.includes(q) || x.tag.toUpperCase().includes(q.toUpperCase()) || x.desc.includes(q)) && (!rec || x.recommended);
  const allOf = (n: Node): Tag[] => [...n.items, ...n.sub.flatMap(allOf)];

  const NodeV = ({ n, lvl }: { n: Node; lvl: number }) => {
    const ps = n.items.filter(hit);
    const subs = n.sub.filter((s) => allOf(s).some(hit));
    if (!ps.length && !subs.length) return null;
    const all = allOf(n).filter((x) => hit(x) && !x.is_target);
    const nsel = all.filter((x) => sel.has(x.tag)).length;
    const isOpen = !!q || rec || openK.has(n.key);
    return (
      <div className={"pkn" + (isOpen ? " open" : "")}>
        <div className={"pkh l" + lvl}>
          <button className="pkc" aria-label="展开或收起" onClick={() => setOpenK((o) => { const s = new Set(o); s.has(n.key) ? s.delete(n.key) : s.add(n.key); return s; })}>{isOpen ? "▾" : "▸"}</button>
          <GroupBox checked={nsel > 0 && nsel === all.length} indeterminate={nsel > 0 && nsel < all.length} label={"全选 " + n.name}
            onChange={(c) => setSel((o) => { const s = new Set(o); all.forEach((x) => (c ? s.add(x.tag) : s.delete(x.tag))); return s; })} />
          <b>{n.name}</b><span className="note">{nsel ? nsel + " / " : ""}{all.length} 个参数</span>
        </div>
        <div className="pkk">
          {ps.map((x) => x.is_target ? (
            <label key={x.tag} className="pkp dis"><input type="checkbox" disabled /><span className="pn">{x.name}</span><span className="pt">{x.tag} · {x.desc}</span><span className="tag t-acc">目标</span></label>
          ) : (
            <label key={x.tag} className="pkp"><input type="checkbox" checked={sel.has(x.tag)} onChange={(e) => setSel((o) => { const s = new Set(o); e.target.checked ? s.add(x.tag) : s.delete(x.tag); return s; })} />
              <span className="pn">{x.name}</span><span className="pt">{x.tag} · {x.desc}</span>{x.recommended ? <span className="tag t-teal">推荐</span> : null}</label>
          ))}
          {subs.map((s) => <NodeV key={s.key} n={s} lvl={lvl + 1} />)}
        </div>
      </div>
    );
  };

  return (
    <Modal open={open} onClose={onClose} wide>
      <h2>选择参数 <span className="sub">按设备浏览，参数后面是对应的测点</span><button style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button></h2>
      <div className="row" style={{ marginBottom: 10 }}>
        <input type="text" autoFocus placeholder="搜索参数名称、位号或测点描述" style={{ flex: 1, minWidth: 200 }} value={q} onChange={(e) => setQ(e.target.value.trim())} />
        <label className="row note" style={{ gap: 6, cursor: "pointer" }}><input type="checkbox" checked={rec} onChange={(e) => setRec(e.target.checked)} /> 只看模板推荐</label>
        <button onClick={() => setOpenK(new Set(tree.flatMap((d) => [d.key, ...d.sub.map((s) => s.key)])))}>全部展开</button>
      </div>
      <div className="pktree">{tree.map((d) => <NodeV key={d.key} n={d} lvl={0} />)}
        {!tags.some(hit) && <p className="note" style={{ padding: 14 }}>没有匹配的参数</p>}</div>
      <div className="row" style={{ marginTop: 12 }}><span className="note">已选 {sel.size} 个参数</span><button style={{ marginLeft: "auto" }} onClick={onClose}>取消</button>
        <button className="pri" onClick={() => onOk(tags.filter((t) => sel.has(t.tag)).map((t) => t.tag))}>确定</button></div>
    </Modal>
  );
}

function GroupBox({ checked, indeterminate, label, onChange }: { checked: boolean; indeterminate: boolean; label: string; onChange: (c: boolean) => void }) {
  const r = useRef<HTMLInputElement>(null);
  useEffect(() => { if (r.current) r.current.indeterminate = indeterminate; });
  return <input ref={r} type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} />;
}
