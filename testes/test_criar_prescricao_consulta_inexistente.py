def test_criar_prescricao_consulta_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.post(
        "/prescricoes/",
        headers=headers,
        json={
            "consulta_id": 999999,
            "medicamento": "Amoxicilina",
            "dosagem": "500mg",
            "frequencia": "12h",
            "duracao": "7 dias",
            "observacoes": "Teste"
        }
    )

    assert response.status_code == 200

    assert response.json() == {
        "erro": "Consulta nao encontrada"
    }