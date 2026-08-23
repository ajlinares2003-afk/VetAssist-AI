def test_atualizar_usuario_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.put(
        "/usuarios/999999",
        headers=headers,
        json={
            "nome": "Teste",
            "email": "teste@email.com",
            "senha_hash": "123456",
            "perfil": "ADMIN"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Usuario nao encontrado"
    }