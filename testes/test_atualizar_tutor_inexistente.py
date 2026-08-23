def test_atualizar_tutor_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/tutores/999999",
        headers=headers,
        json={
            "nome": "Tutor Teste",
            "cpf": "12345678900",
            "telefone": "11999999999",
            "email": "teste@email.com",
            "endereco": "Rua Teste"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Tutor nao encontrado"
    }