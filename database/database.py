import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

# Lê a variável de ambiente do Render/Supabase, ou usa o localhost por defeito no seu PC
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:VetAssist%402026@localhost:5432/vetassist_ai")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()