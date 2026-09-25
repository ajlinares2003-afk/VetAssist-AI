from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import traceback
import google.generativeai as genai
from openai import OpenAI

# Lê a URL da base de dados do ambiente (Supabase na nuvem)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

# Ajuste automático caso a string venha do Supabase com parâmetros específicos
if "postgresql://" in DATABASE_URL and "?" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

router = APIRouter(prefix="/configuracoes", tags=["Configurações de IA"])

@router.get("/ia")
def obter_configuracoes_ia():
    resultado = {
        "groq_model_1": "openai/gpt-oss-120b",
        "groq_model_2": "qwen/qwen3.8-27b",
        "gemini_model": "gemini-3.6-flash"
    }
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT chave, valor FROM configuracoes_sistema")).fetchall()
        for r in rows:
            resultado[r[0]] = r[1]
    except Exception as e:
        print(f"Aviso ao ler configurações da BD: {e}")
    finally:
        db.close()
    return resultado

@router.put("/ia")
def atualizar_configuracoes_ia(payload: dict):
    print("=== INICIO PUT /configuracoes/ia ===")
    print(f"Payload recebido: {payload}")
    
    db = SessionLocal()
    try:
        for chave, valor in payload.items():
            if valor is not None:
                db.execute(
                    text("DELETE FROM configuracoes_sistema WHERE chave = :c"),
                    {"c": str(chave)}
                )
                db.execute(
                    text("INSERT INTO configuracoes_sistema (chave, valor) VALUES (:c, :v)"),
                    {"c": str(chave), "v": str(valor)}
                )
        db.commit()
        print("Configurações atualizadas com sucesso no Supabase!")
        return {"mensagem": "Configurações de IA atualizadas com sucesso!"}
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        print(f"ERRO SQL DETALHADO: {err_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro no Banco de Dados: {err_msg}")
    finally:
        db.close()

@router.post("/ia/testar")
def testar_modelo_ia(payload: dict):
    provedor = str(payload.get("provedor", "")).lower()
    modelo = str(payload.get("modelo", ""))
    try:
        if provedor == "groq":
            client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY")
            )
            completion = client.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": "Responda apenas: 'Conexao bem sucedida!'"}]
            )
            return {"sucesso": True, "resposta": completion.choices[0].message.content}
        elif provedor == "gemini":
            api_key = os.getenv("GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(modelo)
            response = model.generate_content("Responda apenas: 'Conexao bem sucedida!'")
            return {"sucesso": True, "resposta": response.text}
        else:
            return {"sucesso": False, "erro": "Provedor desconhecido"}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}