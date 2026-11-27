import { Component, ReactNode } from "react";

// 页面出错时显示错误信息，而不是整个界面空白；切换到其他页面后自动恢复
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) {
    return { err };
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.err) this.setState({ err: null });
  }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="c" style={{ borderColor: "var(--bad)" }}>
        <h2 style={{ color: "var(--bad)" }}>这个页面出错了</h2>
        <p style={{ margin: "0 0 8px" }}>其他页面不受影响，可以从左侧切换。请把下面的错误信息发给开发人员：</p>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, background: "var(--soft)", padding: 10, borderRadius: 6, margin: 0 }}>{String(this.state.err.stack || this.state.err.message).slice(0, 1500)}</pre>
        <div className="row" style={{ marginTop: 10 }}><button onClick={() => this.setState({ err: null })}>重试</button></div>
      </div>
    );
  }
}
