"""前端静态文件的类型必须正确，不能依赖操作系统（Windows 注册表）里的设置。"""
import mimetypes
import re

import pytest

from app import config


@pytest.mark.skipif(not (config.FRONTEND_DIST / "index.html").exists(), reason="前端未构建")
def test_js_served_as_javascript(client):
    mimetypes.add_type("text/plain", ".js")  # 模拟 Windows 上的错误登记
    from importlib import reload
    import app.main
    reload(app.main)  # 重新执行启动时的类型登记
    html = client.get("/").text
    js = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
    assert client.get(js).headers["content-type"].startswith("text/javascript")
