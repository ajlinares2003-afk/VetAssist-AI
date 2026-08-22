from database import engine
try:
    with engine.connect() as connection:
        print("Conexão com PostgreSQL realizada com sucesso!")
except Exception as e:
    print(f"Erro: {e}")