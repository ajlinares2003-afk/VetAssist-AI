def test_login_invalido(client):

    response = client.post(
        "/auth/login",
        data={
            "username": "email_inexistente@email.com",
            "password": "123456"
        }
    )

    assert response.status_code == 401