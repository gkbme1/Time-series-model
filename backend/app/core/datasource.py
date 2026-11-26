"""数据源接口。

Demo 全部从本地文件读取（FileDataSource）。以后接 XBase 实时库 / 历史库时，
实现同样的接口替换即可。上线回放用的 ReplayDataSource 在阶段 4 加入，
它包装任一数据源并拒绝读取晚于回放时钟的数据。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .. import config


class DataSource(ABC):
    @abstractmethod
    def tag_table(self) -> pd.DataFrame:
        """参数表：tag, name, device, part, desc, unit, period, range_lo, range_hi, recommended, is_target, furnace"""

    @abstractmethod
    def read(self, furnace: str, tags: list[str], start=None, end=None) -> pd.DataFrame:
        """宽表，索引为 1 分钟时间戳，缺失为 NaN。"""

    @abstractmethod
    def span(self, furnace: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        ...


class FileDataSource(DataSource):
    def __init__(self, data_dir: Path | None = None):
        self.dir = Path(data_dir or config.DATA_DIR)
        self._lock = threading.Lock()
        self._frames: dict[str, pd.DataFrame] = {}
        self._tags: pd.DataFrame | None = None

    def tag_table(self) -> pd.DataFrame:
        if self._tags is None:
            t = pd.read_csv(self.dir / "tags.csv", dtype={"part": str}, encoding="utf-8-sig").fillna({"part": ""})
            self._tags = t
        return self._tags

    def _frame(self, furnace: str) -> pd.DataFrame:
        with self._lock:
            if furnace not in self._frames:
                df = pd.read_parquet(self.dir / "timeseries" / f"{furnace}.parquet")
                df = df.set_index("timestamp").sort_index().astype("float64")
                self._frames[furnace] = df
            return self._frames[furnace]

    def read(self, furnace, tags, start=None, end=None):
        df = self._frame(furnace)
        out = df.reindex(columns=list(tags))
        if start is not None or end is not None:
            out = out.loc[start:end]
        return out

    def span(self, furnace):
        idx = self._frame(furnace).index
        return idx[0], idx[-1]


@lru_cache(maxsize=1)
def default_source() -> FileDataSource:
    return FileDataSource()
