from fastapi.testclient import TestClient
from main import app

def test_buscar_usuario_inexistente(client):

    response = client.get("/usuarios/9999")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Usuario nao encontrado"
    }