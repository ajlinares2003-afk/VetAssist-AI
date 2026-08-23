def test_criar_animal_com_tutor_inexistente(
    client,
    token_recepcao
):

    headers = {
        "Authorization": f"Bearer {token_recepcao}"
    }

    response = client.post(
        "/animais/",
        headers=headers,
        json={
            "nome": "Bolt",
            "especie": "Canino",
            "raca": "SRD",
            "sexo": "M",
            "idade": 3,
            "peso": 12,
            "tutor_id": 9999,
            "status": "ATIVO"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Tutor nao encontrado"
    }