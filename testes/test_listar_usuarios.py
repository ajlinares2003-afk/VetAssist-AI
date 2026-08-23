def test_listar_usuarios(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.get(
        "/usuarios/",
        headers=headers
    )

    assert response.status_code == 200

    assert isinstance(
        response.json(),
        list
    )