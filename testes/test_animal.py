from fastapi.testclient import TestClient
from main import app

from conftest import client
def test_buscar_animal_inexistente(client):

    response = client.get("/animais/9999")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Animal nao encontrado"
    }