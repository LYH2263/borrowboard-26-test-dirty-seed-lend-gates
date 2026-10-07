import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # db.db_path() 在每次 connect 时实时读 DATA_DIR，因此每个用例拿到全新种子库。
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.main import app
    with TestClient(app) as c:
        yield c
