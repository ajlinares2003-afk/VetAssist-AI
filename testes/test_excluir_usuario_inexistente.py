def test_excluir_usuario_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.delete(
        "/usuarios/999999",
        headers=headers
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Usuario nao encontrado"
    }
