def test_buscar_exame_inexistente(client):

    response = client.get(
        "/exames/999999"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Exame nao encontrado"
    }