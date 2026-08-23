def test_atualizar_consulta_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/consultas/999999",
        headers=headers,
        json={
            "usuario_id": 1,
            "animal_id": 1,
            "queixa_principal": "Teste",
            "historico_clinico": "Teste",
            "sintomas": "Teste",
            "exame_fisico": "Teste",
            "peso_atendimento": 10,
            "temperatura": 38.5,
            "frequencia_cardiaca": 100,
            "frequencia_respiratoria": 20,
            "observacoes": "Teste"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Consulta nao encontrada"
    }
