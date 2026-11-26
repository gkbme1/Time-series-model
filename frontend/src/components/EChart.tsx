import * as echarts from "echarts";
import { useEffect, useRef } from "react";

type Props = {
  option: echarts.EChartsOption;
  height: number;
  onEvents?: Record<string, (p: any, chart: echarts.ECharts) => void>;
  notMerge?: boolean;
};

// ECharts 薄封装：随容器尺寸自适应，事件透传
export function EChart({ option, height, onEvents, notMerge = true }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const ch = useRef<echarts.ECharts>();
  const evRef = useRef(onEvents);
  evRef.current = onEvents;
  useEffect(() => {
    const c = echarts.init(el.current!, undefined, { renderer: "canvas" });
    ch.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current!);
    const names = ["datazoom", "click", "brushEnd", "brushselected"];
    names.forEach((n) => c.on(n, (p: any) => evRef.current?.[n]?.(p, c)));
    return () => { ro.disconnect(); c.dispose(); };
  }, []);
  useEffect(() => { ch.current?.setOption(option, { notMerge }); }, [option, notMerge]);
  useEffect(() => { ch.current?.resize(); }, [height]);
  return <div ref={el} style={{ width: "100%", height }} />;
}
