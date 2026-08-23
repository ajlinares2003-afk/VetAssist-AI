def test_criar_usuario_sem_token(client):

    response = client.post(
        "/usuarios/",
        json={
            "nome": "Teste",
            "email": "teste@email.com",
            "senha_hash": "123456",
            "perfil": "ADMIN"
        }
    )

    assert response.status_code == 401