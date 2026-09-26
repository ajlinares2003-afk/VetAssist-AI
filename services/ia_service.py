import os
from sqlalchemy import text
from database.database import SessionLocal
import google.generativeai as genai
from openai import OpenAI

def obter_cliente_ia(provedor_desejado: str = "groq_1"):
    db = SessionLocal()
    try:
        if "groq" in provedor_desejado:
            chave_nome = "groq_api_key_2" if "2" in provedor_desejado else "groq_api_key_1"
            modelo_chave = "groq_model_2" if "2" in provedor_desejado else "groq_model_1"
            
            r_mod = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = :c"), {"c": modelo_chave}).fetchone()
            r_key = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = :c"), {"c": chave_nome}).fetchone()
            
            modelo = r_mod[0] if r_mod and r_mod[0] else ("qwen/qwen3.8-27b" if "2" in provedor_desejado else "openai/gpt-oss-120b")
            key_db = r_key[0] if r_key and r_key[0] and not str(r_key[0]).startswith("****") else None
            api_key = key_db or os.getenv("GROQ_API_KEY")
            
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
            return "groq", client, modelo
        else:
            r_mod = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_model'")).fetchone()
            r_key = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_api_key'")).fetchone()
            
            modelo = r_mod[0] if r_mod and r_mod[0] else "gemini-3.6-flash"
            key_db = r_key[0] if r_key and r_key[0] and not str(r_key[0]).startswith("****") else None
            api_key = key_db or os.getenv("GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")
            
            genai.configure(api_key=api_key)
            client = genai.GenerativeModel(modelo)
            return "gemini", client, modelo
    finally:
        db.close()