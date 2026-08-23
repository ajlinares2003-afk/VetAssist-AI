def test_recepcao_excluir_usuario(
    client,
    token_recepcao
):
    headers = {
        "Authorization": f"Bearer {token_recepcao}"
    }

    response = client.delete(
        "/usuarios/3",
        headers=headers
    )

    assert response.status_code == 403