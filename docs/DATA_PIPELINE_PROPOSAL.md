# Proposta: pipeline de dados brutos → dataset limpo com proveniência

> Status: **proposta, não implementada**
> Escopo: `data.json` (o arquivo de contagem de ocupação) + o script `get_data.py`

---

## 1. Diagnóstico do estado atual

### 1.1 Formato real do arquivo

Apesar do nome, `data.json` **não é JSON** — é CSV sem cabeçalho, uma linha por amostra:

```
2024-01-02 16:10:48,57
2024-01-02 16:31:46,56
```

- **79.207 linhas**, de `2024-01-02 16:10:48` a `2026-10-05 15:21:06`
- **79.134 válidas** (timestamp + inteiro), **73 inválidas** (timestamp + valor vazio)
- 0 linhas com texto de erro **no arquivo atual**
- Ordenado cronologicamente, sem timestamps duplicados
- Rastreado por Git LFS

### 1.2 O problema 1 — linhas de erro

**Hoje não há linhas de erro no arquivo.** Elas existiram e foram purgadas manualmente em
`53c5bbf93` ("Fix zero/concat bugs and add retry logic in get_data.py", 2026-06-05), que removeu
192 linhas (184 de erro + 8 zeradas).

O formato que existia e **quebra pandas**:

```
2025-05-18 05:45:16,{'error': {'code': '503', 'message': 'The deployment is currently unavailable'}}
```

Verificado: essa linha gera 3 campos CSV por causa das vírgulas internas, e com
`pd.read_csv(..., names=['ts','v'])` o pandas lança
`ParserError: Expected 2 fields in line 2, saw 3`.

**Raiz histórica:** o commit `833442cf5` fazia
`f.write(... + str(response.json()) + '\n')` sem validar — quando a API respondia um dict de erro
(503/504 da Cloudflare/deploy), o dict era serializado direto no arquivo. Existiam **184 linhas
assim** no auge.

**Hoje sobram 73 linhas com valor vazio:**

```
2024-05-14 10:15:18,
```

Essas não lançam exceção — viram `NaN` silenciosamente (`.dtype = float64`, 73 nulos). Quebram
qualquer coisa que faça `int(linha.split(',')[1])` à moda antiga, e poluem estatísticas.

Além disso, o `get_data.py` **atual já não deve gerar esse lixo** (valida antes de escrever),
mas não há nada que garanta isso mecanicamente — a validação vive no mesmo lugar que a escrita.

### 1.3 O problema 2 — grade temporal irregular

A cron externa dispara a cada 15 min. Mapeei os dados contra essa grade:

| Métrica | Valor |
|---|---|
| Slots de 15 min esperados (2024-01-02 → hoje) | **96.670** |
| Slots com observação | **79.116** (81,8 %) |
| Slots faltando | **17.554** (18,2 %) |
| Observações dentro do marco (≤ 60 s) | 78.840 (99,63 %) |
| Observações fora do marco (minutos diferentes) | 294 (0,37 %) |

**Distribuição dos faltantes por tamanho de buraco:**

| Classe | Slots faltantes | % do total faltante |
|---|---:|---:|
| ≤ 1 h | 218 | 1,2 % |
| 1–6 h | 138 | 0,8 % |
| 6–24 h | 234 | 1,3 % |
| 1–7 dias | 97 | 0,6 % |
| **> 7 dias (2 blocos)** | **16.867** | **96,1 %** |

Os **dois grandes buracos** que você mencionou:

1. `2026-02-08 18:00` → `2026-06-05 22:22` — **117 dias**, ~11.248 slots
2. `2026-08-08 02:15` → `2026-10-05 15:21` — **58 dias**, ~5.619 slots (o outage do endpoint 404)

Os 687 slots "normais" somam **0,7 % da série**. Os dois blocos gigantes somam **17,4 % da série
inteira**. Essa assimetria é central para a decisão de modelagem (ver §4.4).

### 1.4 Fora de grid

Das 294 observações fora do marco de 15 min, **282 caem em slots que não têm nenhuma outra
observação** — ou seja, são a única medida daquele slot. Descartá-las criaria buracos novos
desnecessariamente.

Concentram-se no início do projeto (jan–mar 2024, quando era executado manualmente, antes da cron).

### 1.5 Duplicatas

4 slots têm 2 observações (ex.: `2026-02-08 00:30` com `166` e `168` no mesmo slot). Diferença
mínima, mas precisa de regra de consolidação.

### 1.6 Bug lateral encontrado nos notebooks

```python
# notebooks/02_exploratory_analysis.ipynb e 03_modeling.ipynb
occupancy_df = pd.read_csv('data.json')   # ← sem header=None
```

Como o arquivo não tem cabeçalho, a **primeira linha de dados vira nome de coluna** e some do
dataset: `shape = (79206, 2)`, `columns = ['2024-01-02 16:10:48', '57']`.
Só o `04_causal_forecasting.ipynb` lê corretamente (`header=None, names=[...]`).

---

## 2. Sazonalidade observada (insumo para o modelo)

A série tem **dupla sazonalidade forte e estável** — isso é o que torna a interpolação viável.

**Diária** (média por hora):

```
04h 146  ← pico      12h  58
05h 143              15h  45  ← vale
06h 134              16h  44
09h  91              17h  45
```

**Semanal** (média por dia):

```
sáb 168  ← pico      ter  51  ← vale
dom 130              qua  51
seg  85              qui  56
```

**Estabilidade entre anos** (correlação do perfil de 96 slots):

| | corr vs 2024 | nível médio |
|---|---|---|
| 2025 | **0,998** | 88,9 |
| 2026 | **0,916** | 77,2 |

O **padrão de forma** é muito estável; o **nível** caiu ~15 % desde 2024. Isso indica modelo
**multiplicativo** com nível local, não aditivo global.

**Outros achados:**
- 46 valores `0` — vários são claramente artefato de leitura (`228 → 0 → 246`)
- 420 pontos com desvio > 10×MAD da sua célula (hora, dia-da-semana)
- Sem DST: `America/Sao_Paulo` tem offset fixo `-03:00` desde 2019 → timestamps consistentes
- `prophet>=1.1.5` já está no `requirements.txt`

---

## 3. Arquitetura proposta

Dois artefatos, papéis distintos:

```
raw  ──(append-only, imutável)──►  data.json
                                        │
                                        │  build_clean.py  (determinístico, idempotente)
                                        ▼
clean ──(derivado, regenerável)─►  data/processed/occupancy_clean.parquet
                                        │  + data/processed/occupancy_clean.csv
                                        └─ + data/processed/build_report.json
```

- **`data.json` continua sendo a fonte de verdade**, append-only. Nunca é reescrito pelo pipeline.
- **Dataset limpo é 100 % derivado** — pode ser regenerado do zero a qualquer momento. Se sair
  errado, apaga e roda de novo. Sem risco de perder dado bruto.
- O `build_report.json` documenta o que foi descartado e o que foi sintetizado, auditável.

### 3.1 Limpeza pontual do bruto (uma vez)

Uma única execução, versionada, para remover as 73 linhas com valor vazio. Idempotente: rodar de
novo não muda nada. Justificativa: você pediu para limpar, e linhas vazias não carregam
informação.

O que **não** fazemos: reescrever para "consertar" linhas antigas de erro — elas já não existem.

### 3.2 Validador de escrita (defesa em profundidade)

No `get_data.py`, separar **obter → validar → persistir**:

```python
def fetch_count() -> int: ...          # só retorna int, ou lança
def append_sample(path, value): ...    # só aceita int, formata e escreve
```

Se a validação estiver embutida no loop de escrita (como hoje), um refator futuro pode reabrir a
brecha. Separando, o caminho de escrita **não tem como** produzir linha malformada. Teste unitário
garante: `append_sample(12)` → linha válida; `append_sample({...})` → `TypeError`, nada escrito.

---

## 4. Construção do dataset limpo

### 4.1 Estágio A — parse tolerante

Para cada linha:

| Padrão | Ação |
|---|---|
| `^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+)$` | **aceita** |
| timestamp + valor vazio / não numérico | **rejeita** → log em `build_report.json` |
| dict de erro (`{'error': ...}`) | **rejeita** → log |
| linha sem timestamp (bloco de stacktrace) | **rejeita** → log |
| resto | **rejeita** → log (com número da linha e conteúdo) |

Nada é descartado em silêncio — tudo vai para `rejected_lines[]` no relatório, com
`{linha, motivo, conteudo}`.

### 4.2 Estágio B — consolidar na grade de 15 min

1. **Alinhar ao slot**: `slot = floor(timestamp, 15min)` em `America/Sao_Paulo`
2. **Observação no marco** (≤ 60 s do início do slot) → `source = "original"`
3. **Observação fora do marco** → atribui ao slot mais próximo, mantém o valor (é dado real),
   marca `off_grid = true`. *Racional: 282 dos 294 são a única medida do slot — descartar seria
   trocar dado por ficção.* Alternativa conservadora em §6.
4. **Duplicatas no mesmo slot** → fica a **mediana** (com `n_obs` registrado). Mediana porque
   protege contra um dos dois ser artefato.
5. **Reindexar** para `date_range(start, end, freq="15min")` — slots sem observação viram `NaN`
   temporário, prontos para o estágio C.
6. **Ruído detectável** (zeros isolados, desvio > 10×MAD) → vira `NaN` + entra no relatório como
   `suspect_values[]`. *Não são apagados do bruto, só marcados aqui.*

### 4.3 Estágio C — classificar `original` × `synthetic`

Coluna `source`, dois valores:

- **`original`** — havia observação real no slot
- **`synthetic`** — valor produzido pelo modelo

Complementos de proveniência:

| coluna | tipo | exemplo | significado |
|---|---|---|---|
| `ts` | datetime64[ns, tz] | `2026-10-05 15:15-03:00` | slot de 15 min |
| `value` | float | `61.0` | valor |
| `source` | category | `original` / `synthetic` | origem |
| `method` | category | `observed` / `linear` / `seasonal` / `gap-marked` | como foi obtido |
| `gap_id` | int | `0` se original, senão id do buraco | agrupa buracos contíguos |
| `gap_size` | int | `4` | slots no buraco (0 p/ original) |
| `off_grid` | bool | `true` | original que veio fora do marco |
| `confidence` | float 0–1 | `1.0` / `0.62` | ver §5 |

`gap_id` permite, num `df[df.gap_id > 0]`, isolar **todo** dado sintético de uma vez — e
`df[df.source == 'original']` recupera exatamente a série bruta. Nenhum processo downstream é
obrigado a saber da interpolação.

### 4.4 Estágio D — interpolação, por tamanho de buraco

Um único modelo não serve para gaps de 1 slot e de 117 dias. Proposta em **tiers**:

#### Tier 1 — gaps curtos (≤ 4 slots / ≤ 1 h): interpolação linear no resíduo sazonal

Interpolação linear pura atravessando a curva diária erra sistemático (gap das 02h→05h: o real sobe
até o pico das 04h e desce; a reta dá errado). Então:

```
1. y_hat(t) = nível_local(t) × S(weekday(t), slot(t))    # perfil sazonal
2. r(t)     = y(t) − y_hat(t)                            # resíduo
3. interpolar r linearmente entre os vizinhos observados
4. y_limpo(t) = y_hat(t) + r_interp(t)
```

Para gaps de 1–4 slots o resíduo é quase plano, então a interpolação linear nele é muito melhor
que no valor bruto. **Sem dependência nova.**

#### Tier 2 — gaps médios (1 h – 7 dias): modelo sazonal + resíduo

Mesmo `y_hat`, mas o nível vem de **janela local (±14 dias)** e o perfil `S` de
`(weekday, slot)` calculado pela mediana dos `y/y_hat` daquela janela. O resíduo é interpolado
entre os extremos do buraco.

Essa classe tem **669 slots** (0,7 % da série) — é onde a interpolação realmente importa.

#### Tier 3 — os 2 blocos > 7 dias: **decisão em aberto** (§6)

16.867 slots = 96 % dos faltantes, 17,4 % da série. Ver §4.5.

#### Declaração de Tier 0 — linear puro (baseline)

Para comparação honesta no backtest (§5). Nunca é a escolha final, mas dá o número de referência.

### 4.5 Por que Prophet não é a escolha aqui

`prophet` está no requirements, então considerei. Contra:

1. **Para os 2 blocos gigantes** — Prophet num gap de 117 dias *extrapolaria* a sazonalidade
   plausivelmente, mas seria **invenção com cara de previsão**. Não há informação real ali.
2. **Determinismo** — pode variar entre execuções (otimização, seed). Isso geraria churn no git a
   cada build, com o mesmo input produzindo arquivo diferente.
3. **Custo** — 80k pontos, 96 sazonalidades, treinado a cada 15 min.

Recomendo: **modelo sazonal por médias robustas (§4.3) no pipeline**, Prophet reservado para
análise exploratória fora do caminho crítico. Se quiser Prophet no Tier 3, dá para fixar seed e
tratar como gerador único do dataset — ver §6.

---

## 5. Validação — backtesting antes de confiar

Interpolação sem métrica é chute. Proposta: **mascarar dado real conhecido e medir o erro.**

1. Sortear 5 % dos slots `original`, em **blocos** de tamanho 1, 4, 96 e 672 slots
   (replicando a assinatura dos buracos reais)
2. Rodar cada tier sobre esses buracos artificiais
3. Medir **MAE, MAPE e P90 do erro absoluto**, por tier e por horário do dia
4. Escolher a variante de cada tier pelo menor erro — e **documentar a escolha no relatório**

Critério de aceite sugerido:

| Tier | MAPE alvo |
|---|---|
| 1 (≤ 1 h) | < 5 % |
| 2 (1 h – 7 d) | < 10 % |

Backtest roda no build (é barato) e o resultado vai para `build_report.json`. Se um dia o erro
estourar o limite, o build **fica vermelho** em vez de entregar dataset ruim em silêncio.

---

## 6. Decisões em aberto

### D1 — o que fazer com os 2 blocos > 7 dias? **(a mais importante)**

| Opção | O que faz | Contra |
|---|---|---|
| **D1a. `gap-marked`** ⭐ | Slots ficam `NaN`, `source = "missing"`, série não é contínua | Você perde continuidade para forecasting |
| **D1b. Sazonal pura** | Preenche com `y_hat` (perfil do período correspondente do ano), `synthetic`, `confidence = 0.2` | 16.867 valores inventados; nível de 2026 difere de 2025 |
| **D1c. Linear** | Reta de 117 dias entre os extremos | Tecnicamente inaceitável — a série oscila, a reta não |
| **D1d. Prophet/STL** | Modelo pleno, `synthetic`, `confidence = 0.1` | Não determinístico; melhor plausibilidade, mesma falta de dado |

**Recomendo D1a.** Motivo: 17 % da série sendo sintético distorce qualquer análise que não filtre
por `source`, e nenhum modelo recupera 117 dias de informação perdida — só mascara. Com `D1a` os
dados estão lá, marcados, e quem quiser preencher faz downstream.

Se a continuidade for essencial ao seu forecasting, **D1b com `confidence` baixa** é o
compromisso honesto — nunca D1c.

### D2 — observações fora do marco

- **D2a.** Atribui ao slot mais próximo, marca `off_grid = true` ⭐ (282 são a única medida do slot)
- **D2b.** Descarta e interpola (seguiu seu pedido literal, mas troca dado real por sintético)

### D3 — onde o build roda

- **D3a.** No workflow, após `get_data.py`, commitando o dataset limpo junto ⭐
  *(atualiza a cada 15 min, ~1–2 s de build, já resolvido o problema do commit vazio)*
- **D3b.** Só sob demanda (`workflow_dispatch` / local)
- **D3c.** Workflow separado, ex. 1x/dia

### D4 — limpeza das 73 linhas vazias do bruto

- **D4a.** Limpar agora, uma vez, com script versionado ⭐ (você pediu)
- **D4b.** Manter o bruto intocável e filtrar só no dataset limpo (auditoria total)

### D5 — formato de saída

- **D5a.** Parquet + CSV ⭐ (parquet tipado e compacto; CSV para quem lê no Excel/pandas avulso)
- **D5b.** Só CSV
- **D5c.** JSON de verdade (dado o nome do arquivo)

---

## 7. Plano de implementação

| # | Tarefa | Depende de |
|---|---|---|
| 1 | Separar `fetch_count()` / `append_sample()` no `get_data.py` + teste | — |
| 2 | `src/pipeline/build_clean.py`: estágios A–D | — |
| 3 | `src/pipeline/backtest.py`: mascarar e medir | 2 |
| 4 | Limpeza pontual das 73 linhas (D4) | D4 |
| 5 | Wire-up no workflow + `git diff --cached --quiet` (já feito) | D3 |
| 6 | Corrigir `pd.read_csv('data.json')` → `header=None, names=[...]` nos notebooks 02/03 | — |
| 7 | Testes: parse, grade, proveniência, determinismo | 2 |

**Critério de aceite global:** rodar o build duas vezes seguidas → arquivos **byte-a-byte idênticos**
(determinismo). E `df[df.source == 'original'].value` tem exatamente os 79.134 valores atuais.

---

## 8. Resumo executivo

1. **O lixo de erro já foi purgado** em junho/2026; sobram **73 linhas vazias** (viram `NaN`
   silencioso). A causa raiz — `str(response.json())` sem validação — **já foi corrigida**; falta
   mecanizar a garantia separando validação de escrita.
2. **18,2 % dos slots de 15 min não têm dado**, mas **96 % disso está em 2 blocos** (117 e 58 dias).
   Os faltantes "normais" são só 0,7 % da série — interpolação é fácil e confiável neles.
3. **A sazonalidade é dupla e estável** (corr 0,998 entre 2024→2025), o que sustenta um modelo
   sazonal multiplicativo simples, **sem Prophet** e sem dependência nova.
4. **A informação de proveniência (`source`, `method`, `gap_id`, `confidence`) vem embutida** —
   nenhum consumidor downstream é obrigado a saber que existe interpolação.
5. **Os 2 blocos gigantes são a decisão que importa**: recomendo marcá-los como `missing` em vez
   de preencher 17 % da série com invenção. Ver D1.
