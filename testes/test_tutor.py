def test_buscar_tutor_inexistente(client):

    response = client.get("/tutores/9999")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Tutor nao encontrado"
    }