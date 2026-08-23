def test_excluir_consulta_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.delete(
        "/consultas/999999",
        headers=headers
    )

    assert response.status_code == 404