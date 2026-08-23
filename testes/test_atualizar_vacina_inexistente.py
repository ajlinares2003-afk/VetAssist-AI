def test_atualizar_vacina_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/vacinas/999999",
        headers=headers,
        json={
            "animal_id": 1,
            "nome_vacina": "Antirrabica",
            "fabricante": "Zoetis",
            "lote": "ABC123",
            "dose": "1 dose",
            "data_aplicacao": "2025-01-01",
            "data_reforco": "2026-01-01",
            "observacoes": "Teste"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Vacina nao encontrada"
    }