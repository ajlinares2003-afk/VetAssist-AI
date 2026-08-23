def test_buscar_prontuario_inexistente(
    client
):
    response = client.get(
        "/prontuarios/999999"
    )

    assert response.status_code == 200

    assert response.json() == {
        "erro": "Animal nao encontrado"
    }