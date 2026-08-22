def test_login_sucesso(client):

    response = client.post(
        "/auth/login",
        data={
            "username": "adilson@email.com",
            "password": "123456"
        }
    )

    assert response.status_code == 200

    assert "access_token" in response.json()