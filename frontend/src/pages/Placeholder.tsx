const PHASE: Record<string, number> = { 分工况: 2, 加工输入: 2, 训练模型: 3, 检验效果: 3, 上线投用: 4, 在线校核: 4, 跟踪改进: 4 };
export default function Placeholder({ name }: { name: string }) {
  return (
    <div className="c ph">
      <h2 style={{ justifyContent: "center", color: "var(--ink)" }}>{name}</h2>
      <p>该页面将在阶段 {PHASE[name] ?? "后续"} 实现。</p>
    </div>
  );
}
