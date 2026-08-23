def test_obter_prontuario(
    client
):
    response = client.get(
        "/prontuarios/4"
    )

    assert response.status_code == 200

    dados = response.json()

    assert "animal" in dados
    assert "tutor" in dados
    assert "vacinas" in dados
    assert "consultas" in dados