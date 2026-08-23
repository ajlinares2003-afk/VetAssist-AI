def test_excluir_prescricao_inexistente(
    client,
    token_admin
):
    headers = {
        "Authorization": f"Bearer {token_admin}"
    }

    response = client.delete(
        "/prescricoes/999999",
        headers=headers
    )

    assert response.status_code == 200

    assert response.json() == {
        "erro": "Prescricao nao encontrada"
    }