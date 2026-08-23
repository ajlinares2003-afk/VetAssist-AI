def test_recepcao_criar_usuario(
    client,
    token_recepcao
):
    headers = {
        "Authorization": f"Bearer {token_recepcao}"
    }

    response = client.post(
        "/usuarios/",
        headers=headers,
        json={
            "nome": "Teste",
            "email": "teste@email.com",
            "senha_hash": "123456",
            "perfil": "ADMIN"
        }
    )

    assert response.status_code == 403