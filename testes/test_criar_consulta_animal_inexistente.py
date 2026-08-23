def test_criar_consulta_animal_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.post(
        "/consultas/",
        headers=headers,
        json={
            "usuario_id": 1,
            "animal_id": 999999,
            "queixa_principal": "Sem apetite",
            "historico_clinico": "Nenhum",
            "sintomas": "Apatia",
            "exame_fisico": "Normal",
            "peso_atendimento": 10,
            "temperatura": 38.5,
            "frequencia_cardiaca": 100,
            "frequencia_respiratoria": 20,
            "observacoes": "Teste"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Animal nao encontrado"
    }