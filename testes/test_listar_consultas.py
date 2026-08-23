def test_listar_consultas(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.get(
        "/consultas/",
        headers=headers
    )

    assert response.status_code == 200

    assert isinstance(
        response.json(),
        list
    )