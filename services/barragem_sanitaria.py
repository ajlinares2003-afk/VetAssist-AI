-- ============================================================================
-- ITEM 1: ampliar espécies e criar a linha de RAIVA para cães, gatos e silvestres.
-- >>> RASCUNHO PARA REVISÃO. NÃO rode antes de um médico-veterinário responsável conferir
-- >>> cada trecho com as normas vigentes (MAPA / Ministério da Saúde / Vigilância local).
-- Idempotente: pode rodar mais de uma vez sem duplicar nada.
-- Rode ANTES o defesa_sanitaria.sql (colunas orgao/sinais/exclusiva).
-- ============================================================================

-- ---------- A) AMPLIAR 'grupos' (só ACRESCENTA; nunca remove) ----------
-- Cada linha: id | doença | espécies acrescentadas | motivo
--   6  Brucelose            + ruminantes, suinos, caninos      (B. melitensis, B. suis, B. canis)
--   7  Tuberculose          + ruminantes, suinos, caninos, felinos, primatas, silvestres
--  24  Botulismo            + caninos, silvestres
--  13  Aujeszky             + ruminantes                       (já tem suínos, bovídeos, cães, gatos)
--  74  Febre Maculosa       + silvestres, primatas
--  76  Tularemia/Triquinelose + lagomorfos, roedores, felinos
--  92  Mpox                 + roedores
-- Confira cada linha: se não concordar, apague a linha do VALUES.
with novos(id, extra) as (values
  (6,  array['ruminantes','suinos','caninos']),
  (7,  array['ruminantes','suinos','caninos','felinos','primatas','silvestres']),
  (24, array['caninos','silvestres']),
  (13, array['ruminantes']),
  (74, array['silvestres','primatas']),
  (76, array['lagomorfos','roedores','felinos']),
  (92, array['roedores'])
)
update public.defesa_sanitaria d
   set grupos = (select array_agg(distinct g order by g) from unnest(d.grupos || n.extra) g)
  from novos n
 where d.id = n.id;

-- ---------- B) LINHA NOVA: Raiva em cães, gatos e silvestres ----------
-- A linha 5 ("Raiva dos Herbívoros") continua valendo para ruminantes e equídeos.
-- Sem 'sinais' de propósito: convulsão e alteração neurológica em cão/gato são comuns
-- (epilepsia, cinomose); suspeita por sinais isolados geraria alarme falso demais.
insert into public.defesa_sanitaria
  (nome, padrao, grupos, exames, aviso, tratamento, conduta, notificacao_confirmada, sem_carencia, exclusiva, orgao)
select
  'Raiva (cães, gatos e silvestres)',
  '\braiva\b|rabies',
  array['caninos','felinos','primatas','silvestres','roedores','lagomorfos','coelhos'],
  'Colheita de tecido encefálico pelo serviço oficial/laboratório de referência; não puncione o SNC de animal vivo.',
  'Zoonose letal de alta gravidade: uso obrigatório de EPI e manipulação apenas por equipe com vacinação pré-exposição.',
  'Não institua tratamento em animal suspeito de raiva.',
  '["Isole o animal suspeito e evite qualquer contato desnecessário.",
    "Se houve mordedura ou arranhadura em pessoa ou outro animal, comunique imediatamente a Vigilância em Saúde: a observação do animal e a profilaxia humana são definidas pelo serviço de saúde.",
    "Uso obrigatório de EPI completo e manipulação exclusiva por equipe vacinada/imunizada.",
    "BIOSSEGURANÇA: a punção de LCR em animal vivo expõe a equipe a risco biológico elevado. Priorize exames não invasivos (sangue/swab), isolamento e notificação oficial; procedimentos no SNC só com equipe imunizada e EPI completo de alta barreira."]'::jsonb,
  false, true, false,
  'Vigilância em Saúde (secretaria municipal/estadual) e serviço veterinário oficial'
where not exists (select 1 from public.defesa_sanitaria where nome = 'Raiva (cães, gatos e silvestres)');

-- ---------- C) LEISHMANIOSE VISCERAL (id 15): texto de tratamento ----------
-- Hoje diz "Sem tratamento clínico autorizado", o que pode estar desatualizado para cães.
-- Proposta (CONFIRME na norma vigente do MAPA/Ministério da Saúde antes de aplicar):
-- update public.defesa_sanitaria
--    set tratamento = 'Tratamento de cães somente com produto veterinário registrado no MAPA e conforme a norma vigente; é proibido usar fármacos de uso humano. Notifique a Vigilância em Saúde.',
--        orgao = 'Vigilância em Saúde (secretaria municipal/estadual) e serviço veterinário oficial'
--  where id = 15;