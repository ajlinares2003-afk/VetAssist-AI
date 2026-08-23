def test_excluir_exame_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.delete(
        "/exames/999999",
        headers=headers
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Exame nao encontrado"
    }