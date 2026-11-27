"""回放时钟：用历史文件模拟实时数据流。

- 时钟有一个"模拟时刻"，按设定倍速前进（60 倍：现实 1 秒 = 模拟 1 分钟）。
- 上线时所有数据都通过 ReplayDataSource 读取，它拒绝读取晚于模拟时刻的数据，
  从机制上保证上线推理不会用到未来数据。
- 时钟状态保存在 var/clock.json，服务重启后接着走。
"""
from __future__ import annotations

import json
import logging
import threading
import time

import pandas as pd

from .. import config
from ..core.datasource import DataSource, default_source

log = logging.getLogger("replay")
DEFAULT_START = "2026-11-01 00:00"
SPEEDS = [60, 600, 3600]
TICK = 0.5  # 现实秒


class FutureDataError(RuntimeError):
    pass


class Clock:
    def __init__(self):
        self.lock = threading.RLock()
        self.sim = pd.Timestamp(DEFAULT_START)
        self.speed = 60
        self.running = False
        self.end = None
        self._load()

    def _path(self):
        return config.VAR_DIR / "clock.json"

    def _load(self):
        try:
            j = json.loads(self._path().read_text("utf-8"))
            self.sim, self.speed = pd.Timestamp(j["sim"]), int(j["speed"])
        except Exception:
            pass

    def save(self):
        try:
            self._path().write_text(json.dumps({"sim": f"{self.sim:%Y-%m-%d %H:%M:%S}", "speed": self.speed}), "utf-8")
        except Exception:
            pass

    def now(self) -> pd.Timestamp:
        with self.lock:
            return self.sim

    def now_min(self) -> pd.Timestamp:
        return self.now().floor("min")

    def data_end(self) -> pd.Timestamp:
        if self.end is None:
            self.end = default_source().span("BA-101")[1]
        return self.end

    def advance(self, seconds: float):
        with self.lock:
            nxt = self.sim + pd.Timedelta(seconds=seconds * self.speed)
            if nxt >= self.data_end():
                nxt = self.data_end()
                self.running = False
            self.sim = nxt

    def set(self, sim=None, speed=None, running=None):
        with self.lock:
            if sim is not None:
                t = pd.Timestamp(sim)
                self.sim = min(max(t, pd.Timestamp("2026-01-02")), self.data_end())
            if speed is not None:
                self.speed = int(speed)
            if running is not None:
                self.running = bool(running)
            self.save()

    def state(self) -> dict:
        with self.lock:
            return {"sim": f"{self.sim:%Y-%m-%d %H:%M:%S}", "speed": self.speed, "running": self.running,
                    "end": f"{self.data_end():%Y-%m-%d %H:%M}", "speeds": SPEEDS}


clock = Clock()


class ReplayDataSource(DataSource):
    """包装任一数据源；读取的结束时刻不能晚于回放时钟。"""

    def __init__(self, inner: DataSource, now_fn):
        self.inner, self.now_fn = inner, now_fn

    def tag_table(self):
        return self.inner.tag_table()

    def span(self, furnace):
        a, _ = self.inner.span(furnace)
        return a, self.now_fn()

    def read(self, furnace, tags, start=None, end=None):
        now = self.now_fn()
        if end is None:
            end = now
        if pd.Timestamp(end) > now:
            raise FutureDataError(f"试图读取 {end}，晚于回放时钟 {now}")
        return self.inner.read(furnace, tags, start, end)


def replay_source() -> ReplayDataSource:
    return ReplayDataSource(default_source(), clock.now_min)


# ----------------------------------------------------------------------------- 时钟线程
_thread = None
_stop = threading.Event()
_hooks: list = []


def on_tick(fn):
    _hooks.append(fn)


def _loop():
    last = time.monotonic()
    while not _stop.is_set():
        time.sleep(TICK)
        now = time.monotonic()
        dt = now - last
        last = now
        if clock.running:
            clock.advance(dt)
            if int(now) % 5 == 0:
                clock.save()
        for h in list(_hooks):
            try:
                h()
            except Exception:
                log.exception("回放处理出错")


def start_thread():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="replay-clock", daemon=True)
    _thread.start()


def stop_thread():
    _stop.set()
