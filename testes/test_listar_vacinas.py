def test_listar_vacinas(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.get(
        "/vacinas/",
        headers=headers
    )

    assert response.status_code == 200

    assert isinstance(
        response.json(),
        list
    )