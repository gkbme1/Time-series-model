import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    var = tmp_path_factory.mktemp("var")
    os.environ["TSM_VAR_DIR"] = str(var)
    from app import config
    config.VAR_DIR = Path(var)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
