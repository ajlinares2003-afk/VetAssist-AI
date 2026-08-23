def test_obter_resumo_prontuario(
    client
):
    response = client.get(
        "/prontuarios/4/resumo"
    )

    assert response.status_code == 200

    dados = response.json()

    assert "animal_id" in dados
    assert "animal" in dados
    assert "resumo" in dados

    assert dados["animal_id"] == 4