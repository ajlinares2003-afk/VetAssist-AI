import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def token_recepcao(client):

    response = client.post(
        "/auth/login",
        data={
            "username": "recepcao@teste.com",
            "password": "123456"
        }
    )

    assert response.status_code == 200

    return response.json()["access_token"]