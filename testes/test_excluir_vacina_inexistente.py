def test_excluir_vacina_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.delete(
        "/vacinas/999999",
        headers=headers
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Vacina nao encontrada"
    }