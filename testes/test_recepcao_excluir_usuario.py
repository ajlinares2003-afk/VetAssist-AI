import pytest

@pytest.fixture
def token_recepcao(client):

    response = client.post(
        "/auth/login",
        data={
            "username": "recepcao@email.com",
            "password": "123456"
        }
    )

    assert response.status_code == 200

    return response.json()["access_token"]


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