from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import traceback
import google.generativeai as genai
from openai import OpenAI

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
if "postgresql://" in DATABASE_URL and "?" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

router = APIRouter(prefix="/configuracoes", tags=["Configurações de IA"])

def obter_chave_armazenada(db, chave_nome, env_nome):
    row = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = :c"), {"c": chave_nome}).fetchone()
    if row and row[0] and str(row[0]).strip() and not str(row[0]).startswith("****"):
        return str(row[0]).strip()
    return os.getenv(env_nome)

@router.get("/ia")
def obter_configuracoes_ia():
    resultado = {
        "groq_model_1": "openai/gpt-oss-120b",
        "groq_api_key_1": "",
        "groq_model_2": "qwen/qwen3.8-27b",
        "groq_api_key_2": "",
        "gemini_model": "gemini-3.6-flash",
        "gemini_api_key": ""
    }
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT chave, valor FROM configuracoes_sistema")).fetchall()
        for r in rows:
            chave, valor = r[0], r[1]
            if "api_key" in chave and valor:
                resultado[chave] = "****" + str(valor)[-4:] if len(str(valor)) > 4 else "****"
            else:
                resultado[chave] = valor
    except Exception as e:
        print(f"Aviso ao ler configurações da BD: {e}")
    finally:
        db.close()
    return resultado

@router.put("/ia")
def atualizar_configuracoes_ia(payload: dict):
    db = SessionLocal()
    try:
        for chave, valor in payload.items():
            if valor is not None:
                # Se for chave mascarada, não sobrescrevemos a original se não foi alterada
                if "api_key" in str(chave) and str(valor).startswith("****"):
                    continue
                db.execute(text("DELETE FROM configuracoes_sistema WHERE chave = :c"), {"c": str(chave)})
                db.execute(text("INSERT INTO configuracoes_sistema (chave, valor) VALUES (:c, :v)"), {"c": str(chave), "v": str(valor)})
        db.commit()
        return {"mensagem": "Configurações de IA atualizadas com sucesso!"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro no Banco de Dados: {str(e)}")
    finally:
        db.close()

@router.post("/ia/testar")
def testar_modelo_ia(payload: dict):
    provedor = str(payload.get("provedor", "")).lower()
    modelo = str(payload.get("modelo", ""))
    db = SessionLocal()
    try:
        if provedor == "groq_1":
            api_key = obter_chave_armazenada(db, "groq_api_key_1", "GROQ_API_KEY")
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
            completion = client.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": "Responda apenas: 'Conexao bem sucedida!'"}]
            )
            return {"sucesso": True, "resposta": completion.choices[0].message.content}
        elif provedor == "groq_2":
            api_key = obter_chave_armazenada(db, "groq_api_key_2", "GROQ_API_KEY")
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
            completion = client.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": "Responda apenas: 'Conexao bem sucedida!'"}]
            )
            return {"sucesso": True, "resposta": completion.choices[0].message.content}
        elif provedor == "gemini":
            api_key = obter_chave_armazenada(db, "gemini_api_key", "GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(modelo)
            response = model.generate_content("Responda apenas: 'Conexao bem sucedida!'")
            return {"sucesso": True, "resposta": response.text}
        else:
            return {"sucesso": False, "erro": "Provedor desconhecido"}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}
    finally:
        db.close()