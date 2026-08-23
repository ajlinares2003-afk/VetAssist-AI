def test_atualizar_prescricao_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/prescricoes/999999",
        headers=headers,
        json={
            "consulta_id": 1,
            "medicamento": "Amoxicilina",
            "dosagem": "500mg",
            "frequencia": "12h",
            "duracao": "7 dias",
            "observacoes": "Teste"
        }
    )

    assert response.status_code == 200

    assert response.json() == {
        "erro": "Prescricao nao encontrada"
    }