def test_atualizar_animal_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/animais/999999",
        headers=headers,
        json={
            "nome": "Bolt",
            "especie": "Canino",
            "raca": "SRD",
            "sexo": "M",
            "idade": 3,
            "peso": 12,
            "tutor_id": 1,
            "status": "ATIVO"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Animal nao encontrado"
    }