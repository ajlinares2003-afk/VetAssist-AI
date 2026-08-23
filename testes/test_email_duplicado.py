def test_email_duplicado(client, token_admin):

    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.post(
        "/usuarios/",
        headers=headers,
        json={
            "nome": "Administrador",
            "email": "adilson@email.com",
            "senha_hash": "123456",
            "perfil": "ADMIN"
        }
    )

    assert response.status_code == 409

    assert response.json() == {
        "detail": "Email ja cadastrado"
    }