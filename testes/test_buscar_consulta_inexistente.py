def test_buscar_consulta_inexistente(
    client
):
    response = client.get(
        "/consultas/999999"
    )

    assert response.status_code == 404