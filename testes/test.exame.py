from fastapi.testclient import TestClient
from main import app

def test_buscar_exame_inexistente(client):

    response = client.get("/exames/9999")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Exame nao encontrado"
     }