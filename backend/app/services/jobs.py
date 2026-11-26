"""后台任务：训练、检验在线程池中执行，进度写入数据库，前端轮询。"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("jobs")
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
_lock = threading.Lock()
_running: dict[str, object] = {}


def submit(key: str, fn, *args):
    def run():
        try:
            fn(*args)
        except Exception:  # 任务自己负责把失败写入数据库；这里兜底记录日志
            log.error("任务 %s 失败\n%s", key, traceback.format_exc())
        finally:
            with _lock:
                _running.pop(key, None)
    with _lock:
        _running[key] = _pool.submit(run)


def running(key: str) -> bool:
    with _lock:
        return key in _running
