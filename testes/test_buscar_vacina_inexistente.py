def test_buscar_vacina_inexistente(client):

    response = client.get(
        "/vacinas/999999"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Vacina nao encontrada"
    }