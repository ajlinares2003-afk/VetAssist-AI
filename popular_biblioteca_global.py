import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("A variável DATABASE_URL não está configurada no ambiente.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base de dados universal massiva cobrindo grandes grupos, raças específicas e sexos
UNIVERSO_VETERINARIO = []

# 1. CÃES (Canis lupus familiaris) - Variações de porte e sexos
racas_caes = [
    ("Cão Miniatura / Toy (ex: Poodle Toy, Chihuahua)", 1.0, 5.0, 38.0, 39.2, 120, 160, 20, 35),
    ("Cão de Médio Porte (ex: Cocker, Beagle, SRD)", 10.0, 25.0, 37.5, 39.0, 90, 130, 15, 30),
    ("Cão de Grande Porte (ex: Labrador, Golden, Pastor Alemão)", 25.0, 45.0, 37.5, 39.0, 70, 110, 12, 25),
    ("Cão Gigante (ex: Mastiff, Dogue Alemão)", 50.0, 90.0, 37.5, 39.0, 60, 100, 10, 20)
]
for raca, p_min, p_max, t_min, t_max, fc_min, fc_max, fr_min, fr_max in racas_caes:
    for sexo in ["macho", "fêmea"]:
        UNIVERSO_VETERINARIO.append({
            "classe_animal": "Mamífero", "grupo": "Canídeos Domésticos", "especie": raca, "nome_cientifico": "Canis lupus familiaris", "sexo": sexo,
            "peso_min": p_min, "peso_max": p_max, "temp_repouso_min": t_min, "temp_repouso_max": t_max, "temp_clinica_min": t_min, "temp_clinica_max": t_max + 0.5,
            "fc_repouso_min": fc_min, "fc_repouso_max": fc_max, "fc_clinica_min": fc_min + 10, "fc_clinica_max": fc_max + 20,
            "fr_repouso_min": fr_min, "fr_repouso_max": fr_max, "fr_clinica_min": fr_min + 5, "fr_clinica_max": fr_max + 10,
            "tpc_ref": "Até 2 segundos", "mucosas_ref": "Normocoradas (róseas) e úmidas", "ecc_ideal": "4 a 5 (Escala 1 a 9)", "fonte_bibliografica": "Ettinger - Textbook of Veterinary Internal Medicine"
        })

# 2. GATOS (Felis catus) - Variações e sexos
racas_gatos = [
    ("Gato Doméstico Comum / SRD", 2.5, 5.5, 37.8, 39.2, 140, 220, 20, 30),
    ("Gato Raça Grande (ex: Maine Coon, Norueguês da Floresta)", 6.0, 11.0, 37.8, 39.2, 120, 200, 18, 28)
]
for raca, p_min, p_max, t_min, t_max, fc_min, fc_max, fr_min, fr_max in racas_gatos:
    for sexo in ["macho", "fêmea"]:
        UNIVERSO_VETERINARIO.append({
            "classe_animal": "Mamífero", "grupo": "Felídeos Domésticos", "especie": raca, "nome_cientifico": "Felis catus", "sexo": sexo,
            "peso_min": p_min, "peso_max": p_max, "temp_repouso_min": t_min, "temp_repouso_max": t_max, "temp_clinica_min": t_min, "temp_clinica_max": t_max + 0.5,
            "fc_repouso_min": fc_min, "fc_repouso_max": fc_max, "fc_clinica_min": fc_min + 20, "fc_clinica_max": fc_max + 20,
            "fr_repouso_min": fr_min, "fr_repouso_max": fr_max, "fr_clinica_min": fr_min + 5, "fr_clinica_max": fr_max + 10,
            "tpc_ref": "Até 2 segundos", "mucosas_ref": "Normocoradas", "ecc_ideal": "4 a 5", "fonte_bibliografica": "Nelson & Couto - Small Animal Internal Medicine"
        })

# 3. EQUINOS, BOVINOS E RUMINANTES
ruminantes_ungulados = [
    ("Equino Puro Sangue / Quarto de Milha", "Equus caballus", 400.0, 600.0, 37.2, 38.2, 28, 44, 8, 16, "Reed - Equine Internal Medicine"),
    ("Bovino de Corte (Bos taurus taurus/indicus)", "Bos taurus", 400.0, 900.0, 37.5, 39.0, 60, 80, 10, 30, "Radostits - Veterinary Medicine"),
    ("Caprino Leiteiro", "Capra hircus", 35.0, 75.0, 38.5, 39.7, 70, 90, 15, 30, "Pugh Sheep & Goat Medicine"),
    ("Ovino de Corte e Lã", "Ovis aries", 45.0, 100.0, 38.3, 39.9, 70, 90, 12, 25, "Pugh Sheep & Goat Medicine")
]
for especie, nc, p_min, p_max, t_min, t_max, fc_min, fc_max, fr_min, fr_max, fonte in ruminantes_ungulados:
    for sexo in ["macho", "fêmea", "indiferente"]:
        UNIVERSO_VETERINARIO.append({
            "classe_animal": "Mamífero", "grupo": "Ungulados e Ruminantes", "especie": especie, "nome_cientifico": nc, "sexo": sexo,
            "peso_min": p_min, "peso_max": p_max, "temp_repouso_min": t_min, "temp_repouso_max": t_max, "temp_clinica_min": t_min, "temp_clinica_max": t_max + 0.5,
            "fc_repouso_min": fc_min, "fc_repouso_max": fc_max, "fc_clinica_min": fc_min + 5, "fc_clinica_max": fc_max + 10,
            "fr_repouso_min": fr_min, "fr_repouso_max": fr_max, "fr_clinica_min": fr_min + 5, "fr_clinica_max": fr_max + 10,
            "tpc_ref": "Até 2 segundos", "mucosas_ref": "Normocoradas", "ecc_ideal": "3", "fonte_bibliografica": fonte
        })

# 4. SILVESTRES, EXÓTICOS, AVES E RÉPTEIS
outros_animais = [
    ("Leão", "Panthera leo", "Mamífero", "Carnívoros Selvagens", 120.0, 250.0, 37.5, 38.8, 40, 60, 10, 20, "Fowler's Zoo"),
    ("Onça-pintada", "Panthera onca", "Mamífero", "Carnívoros Selvagens", 56.0, 100.0, 37.8, 39.0, 50, 75, 12, 22, "Fowler's Zoo"),
    ("Lobo-guará", "Chrysocyon brachyurus", "Mamífero", "Canídeos Silvestres", 20.0, 30.0, 37.8, 39.0, 80, 110, 18, 28, "Fowler's Zoo"),
    ("Capivara", "Hydrochoerus hydrochaeris", "Mamífero", "Roedores Silvestres", 30.0, 65.0, 37.5, 39.0, 60, 90, 15, 30, "Fowler's Zoo"),
    ("Macaco-prego", "Sapajus apella", "Mamífero", "Primatas", 2.0, 4.5, 38.0, 39.5, 100, 150, 25, 40, "Fowler's Zoo"),
    ("Coelho Doméstico", "Oryctolagus cuniculus", "Mamífero", "Lagomorfos", 1.0, 5.0, 38.5, 39.5, 130, 325, 30, 60, "Fowler's Zoo"),
    ("Calopsita", "Nymphicus hollandicus", "Aves", "Psitacídeos", 0.08, 0.12, 39.0, 41.0, 200, 350, 20, 40, "Harrison Avian"),
    ("Papagaio-verdadeiro", "Amazona aestiva", "Aves", "Psitacídeos", 0.35, 0.50, 39.0, 41.0, 150, 250, 15, 30, "Harrison Avian"),
    ("Jabuti-piranga", "Chelonoidis carbonaria", "Répteis", "Quelônios", 2.0, 10.0, 24.0, 32.0, 20, 45, 4, 12, "Mader Reptile")
]
for esp, nc, classe, grupo, p_min, p_max, t_min, t_max, fc_min, fc_max, fr_min, fr_max, fonte in outros_animais:
    for sexo in ["macho", "fêmea", "indiferente"]:
        UNIVERSO_VETERINARIO.append({
            "classe_animal": classe, "grupo": grupo, "especie": esp, "nome_cientifico": nc, "sexo": sexo,
            "peso_min": p_min, "peso_max": p_max, "temp_repouso_min": t_min, "temp_repouso_max": t_max, "temp_clinica_min": t_min, "temp_clinica_max": t_max + 1.0,
            "fc_repouso_min": fc_min, "fc_repouso_max": fc_max, "fc_clinica_min": fc_min + 10, "fc_clinica_max": fc_max + 20,
            "fr_repouso_min": fr_min, "fr_repouso_max": fr_max, "fr_clinica_min": fr_min + 5, "fr_clinica_max": fr_max + 10,
            "tpc_ref": "Até 2 segundos", "mucosas_ref": "Normocoradas", "ecc_ideal": "3 a 4", "fonte_bibliografica": fonte
        })

def popular_banco():
    db = SessionLocal()
    print(f"🚀 A injetar o universo massivo com {len(UNIVERSO_VETERINARIO)} registos na Biblioteca Oficial...")

    try:
        inseridos = 0
        for reg in UNIVERSO_VETERINARIO:
            existe = db.execute(
                text("SELECT id FROM biblioteca_parametros_oficiais WHERE especie = :esp AND nome_cientifico = :nc AND sexo = :sx"),
                {"esp": reg["especie"], "nc": reg["nome_cientifico"], "sx": reg["sexo"]}
            ).fetchone()

            if not existe:
                db.execute(
                    text("""
                        INSERT INTO biblioteca_parametros_oficiais (
                            classe_animal, grupo, especie, nome_cientifico, sexo,
                            peso_min, peso_max, temp_repouso_min, temp_repouso_max,
                            temp_clinica_min, temp_clinica_max, fc_repouso_min, fc_repouso_max,
                            fc_clinica_min, fc_clinica_max, fr_repouso_min, fr_repouso_max,
                            fr_clinica_min, fr_clinica_max, tpc_ref, mucosas_ref, ecc_ideal, fonte_bibliografica
                        ) VALUES (
                            :classe_animal, :grupo, :especie, :nome_cientifico, :sexo,
                            :peso_min, :peso_max, :temp_repouso_min, :temp_repouso_max,
                            :temp_clinica_min, :temp_clinica_max, :fc_repouso_min, :fc_repouso_max,
                            :fc_clinica_min, :fc_clinica_max, :fr_repouso_min, :fr_repouso_max,
                            :fr_clinica_min, :fr_clinica_max, :tpc_ref, :mucosas_ref, :ecc_ideal, :fonte_bibliografica
                        )
                    """),
                    reg
                )
                db.commit()
                inseridos += 1

        print(f"\n✨ Sucesso absoluto! {inseridos} novos registos de animais, raças e sexos injetados na base de dados.")
    except Exception as e:
        db.rollback()
        print(f"❌ Erro crítico: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    popular_banco()