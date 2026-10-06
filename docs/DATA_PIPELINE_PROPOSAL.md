# Proposta: pipeline de dados brutos → dataset limpo com proveniência

> Status: **implementado** (`src/pipeline/clean_raw.py`, `src/pipeline/build_clean.py`,
> `src/pipeline/backtest.py`, `tests/test_pipeline.py`)
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

> 📌 Números apurados em **2026-10-06**. Como a grade vai até a última observação, os totais
> crescem a cada amostra que o cron appenda — trate-os como instantâneo, não como constante.

| Métrica | Valor |
|---|---|
| Slots de 15 min esperados (2024-01-02 → hoje) | **96.754** |
| Slots com observação | **79.117** (81,8 %) |
| Slots faltando | **17.637** (18,2 %) |
| Observações dentro do marco (≤ 60 s do floor) | 78.840 (99,63 %) |
| Observações fora do marco (floor > 60 s) | 295 (0,37 %) |

**Distribuição dos faltantes por tamanho de buraco:**

| Classe | Slots faltantes | % do total faltante |
|---|---:|---:|
| ≤ 1 h | 218 | 1,2 % |
| 1–6 h | 138 | 0,8 % |
| 6–24 h | 317 | 1,8 % |
| 1–7 dias | 97 | 0,6 % |
| **> 7 dias (2 blocos)** | **16.867** | **95,6 %** |

Os **dois grandes buracos** que você mencionou:

1. `2026-02-08 18:00` → `2026-06-05 22:22` — **117 dias**, ~11.248 slots
2. `2026-08-08 02:15` → `2026-10-05 15:21` — **58 dias**, ~5.619 slots (o outage do endpoint 404)

Os **770 slots "normais"** (fora dos 2 blocos) somam **0,8 % da série**. Os dois blocos gigantes
somam **17,4 % da série inteira**. Essa assimetria é central para a decisão de modelagem (ver §4.4).

### 1.4 Fora de grid

Das **295** observações fora do marco de 15 min, **279 caem em slots que não têm nenhuma outra
observação** — ou seja, são a única medida daquele slot. Descartá-las criaria buracos novos
desnecessariamente.

> Detalhe de implementação: a atribuição usa **`floor`** (`slot = ts.floor('15min')`), não o vizinho
> mais próximo. Como a cron dispara em `:00/:15/:30/:45` e a latência é de segundos, quase toda
> observação cai logo **depois** da marca — então `floor` coincide com o vizinho mais próximo na
> prática. `off_grid` marca o que ficou **> 60 s após o floor**.

Concentram-se no início do projeto (jan–mar 2024, quando era executado manualmente, antes da cron).

### 1.5 Duplicatas

**16 slots** têm mais de uma observação (14 com 2, 2 com 3) — 18 observações a mais que precisam
de regra de consolidação (mediana). Ex.: `2026-02-08 00:30` com `166` e `168` no mesmo slot.
Diferença mínima, mas precisa de regra.

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
- **1.063 pontos** com desvio > 10×MAD da sua célula (hora, dia-da-semana) — mas a maioria é
  **evento real**: noites de carnaval chegam a 78 slots seguidos acima do limiar, com valor
  350–420. Remover tudo apagaria dado verdadeiro. Por isso o detector de ruído (§4.2.6) é
  conservador: só marca artefato **isolado** (16 casos), não eventos.
- Sem DST: `America/Sao_Paulo` tem offset fixo `-03:00` desde 2019 → timestamps consistentes
- `prophet` foi testado no backtest (§5) e **removido** — perdeu para os modelos simples

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
3. **Observação fora do marco** → mantém no slot do próprio `floor`, mantém o valor (é dado real),
   marca `off_grid = true`. *Racional: 279 dos 295 são a única medida do slot — descartar seria
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
| `method` | category | `observed` / `linear` / `seasonal_residual` / `weekly_naive` / `seasonal` | como foi obtido |
| `gap_id` | int | `0` se original, senão id do buraco | agrupa buracos contíguos |
| `gap_size` | int | `4` | **tamanho total** do buraco em todos os slots dele (0 p/ original) |
| `off_grid` | bool | `true` | original que veio fora do marco |
| `n_obs` | int | `1` | observações que caíram nesse slot (0 p/ sintético) |
| `confidence` | float 0–1 | `1.0` / `0.79` / `0.30` | 1 − MAPE medido no backtest do tier (§5) |

`gap_size` guarda o tamanho **total** do buraco (não a posição dentro dele), então
`df[df.gap_size > 1344]` isola direto os blocos gigantes.

`gap_id` permite, num `df[df.gap_id > 0]`, isolar **todo** dado sintético de uma vez — e
`df[df.source == 'original']` recupera exatamente a série bruta. Nenhum processo downstream é
obrigado a saber da interpolação.

### 4.4 Estágio D — interpolação, por tamanho de buraco

Um único modelo não serve para gaps de 1 slot e de 117 dias. Os tiers abaixo foram
**calibrados pelo backtest** (§5) — cada faixa usa o modelo de menor erro medido:

| tier | gap (slots) | `method` | MAPE medido | `confidence` |
|---|---|---|---|---|
| 1 | ≤ 4 (≤ 1 h) | `linear` | 2,9 % | 0,97 |
| 2 | 5–192 (1–48 h) | `seasonal_residual` | 21 % | 0,79 |
| 3 | 193–1344 (2–14 d) | `weekly_naive` | 27–33 % | 0,67 |
| 4 | > 1344 (> 14 d) | `seasonal` | 15–29 % | 0,71 |

Acima de **4032 slots (28 dias)** — maior gap validado no backtest — o `confidence` é
limitado a **0,30**, porque o erro não foi medido nessa escala.

Definição dos modelos:

**Tier 1 — `linear`**: interpolação linear pura entre os vizinhos observados. Para gaps de
1–4 slots é imbatível (MAE 1,7–2,3) e trivial.

**Tier 2 — `seasonal_residual`**: interpola o *resíduo*, não o valor bruto.

```
1. y_hat(t) = nível_local(t) × S(weekday(t), slot(t))    # perfil sazonal
2. r(t)     = y(t) − y_hat(t)                            # resíduo
3. interpolar r linearmente entre os vizinhos observados
4. y_limpo(t) = y_hat(t) + r_interp(t)
```

Interpolar linear o valor bruto atravessa a curva diária no sentido errado (gap 02h→05h: o real
sobe até o pico das 04h e desce; a reta dá errado). No resíduo, a curva já foi removida.

**Tier 3 — `weekly_naive`**: repete o valor do **mesmo slot na semana anterior**, andando até
8 semanas atrás até achar um slot observado. É o vencedor em gaps de 2–14 dias porque a série
é extremamente estável semana a semana (perfil 2024×2025, corr = 0,998). Sem dependência nova.

**Tier 4 — `seasonal`**: `y_hat` puro (nível × perfil), sem resíduo. Em gaps de 21–28 dias
interpolar o resíduo entre extremos distantes piora (MAPE 19,4 % vs 15,3 % do sazonal puro).

**Nível local**: mediana móvel de 7 dias (`rolling(96*7, center=True)`). Se a janela cai
inteira dentro de um gap e vira `NaN`, faz fallback para a mediana global — senão um gap de
7 dias propagaria `NaN` para o meio dele (bug encontrado e corrigido durante o backtest).

#### Os 2 blocos > 7 dias — decidido: preencher (§6/D1, opção A)

### 4.5 Por que Prophet não é a escolha aqui

Testei (o `prophet>=1.1.5` estava no requirements antigo). O backtest (§5) confirmou:

1. **Perde para os modelos simples em todos os tamanhos** de gap medido: MAPE 28 % (1 slot),
   39 % (4), 72 % (96), 58 % (672), 65 % (4032) — contra 2,4 % do `linear` no gap de 1 slot.
2. **Custo**: 17 s por gap (treino ~78 s uma vez) contra 0,05 s dos modelos simples. ~400× mais lento.
3. **Determinismo** — pode variar entre execuções (otimização, seed). Isso geraria churn no git a
   cada build, com o mesmo input produzindo arquivo diferente.
4. Para os 2 blocos gigantes, Prophet *extrapolaria* a sazonalidade plausivelmente, mas seria
   **invenção com cara de previsão** — e ainda assim com erro medido pior que o sazonal simples.

Conclusão: **modelo sazonal simples (§4.4) no pipeline.**

**`prophet` foi removido do `requirements.txt` e do `backtest.py`** — sem uso no pipeline e sem
ganho medido. Os números acima ficam registrados em `docs/backtest_results.json` (medidos em
2026-10-06, antes da remoção). Se um dia quiser reavaliar: `pip install prophet` e recolocar a
função `prophet_predict` (commit `19186babe` tem a versão completa).

Testado também (Darts 0.47.0, ainda no backtest como referência externa — não está no
`requirements.txt` pois é só ferramenta de avaliação, não do pipeline):

- `NaiveSeasonal(K=96)` — 46 % no gap de 1 slot, perde para o `weekly_naive` (20 %), que faz a
  mesma ideia com janela de 1 semana em vez de 1 dia.
- `ExponentialSmoothing(seasonal_periods=96)` — instável: 6 % em gap curto, mas 230 % em 24 h,
  883 % em 7 dias, 3108 % em 28 dias. **Rejeitado.**

> Ambos são importados sob `try/except ImportError` — sem `darts` instalado o backtest roda
> normalmente e apenas reporta "sem predicoes validas" para esses dois.

---

## 5. Validação — backtesting antes de confiar

Interpolação sem métrica é chute. O que foi feito: **mascarar dado real conhecido e medir o erro.**

Implementado em `src/pipeline/backtest.py`:

1. Sortear blocos de slots `original` de tamanho 1, 4, 96, 192, 384, 672, 1344, 2016 e 4032
   (20 gaps por tamanho nos rápidos, 5 nos lentos, seed=42 fixa)
2. Cada modelo só vê os dados **fora** do gap mascarado (sem vazamento)
3. Medir **MAE, MAPE e P90** do erro absoluto, por tamanho de gap

### Resultado (MAPE, série real, 2026-10-06)

| gap (slots) | tempo | **linear** | **seasonal_resid** | **weekly_naive** | **seasonal** | prophet | darts_seasonal | darts_exp_smooth |
|---|---|---|---|---|---|---|---|---|
| 1 | 15 min | **2,4 %** | 3,4 % | 20,0 % | 20,4 % | 28,4 % | 46,1 % | 6,2 % |
| 4 | 1 h | **2,9 %** | 3,0 % | 22,1 % | 17,0 % | 38,9 % | 61,1 % | 6,9 % |
| 96 | 24 h | 64,4 % | **21,1 %** | 27,1 % | 22,1 % | 72,3 % | 54,8 % | 229,8 % |
| 192 | 48 h | 63,3 % | 20,1 % | **19,5 %** | 21,6 % | – | – | – |
| 384 | 4 d | 100,8 % | 29,1 % | **26,7 %** | 26,3 % | – | – | – |
| 672 | 7 d | 110,3 % | 54,6 % | **33,2 %** | 51,3 % | 58,5 % | 93,5 % | 882,8 % |
| 1344 | 14 d | 100,6 % | 49,4 % | **29,6 %** | 34,6 % | – | – | – |
| 2016 | 21 d | 81,4 % | 30,7 % | 57,3 % | **29,2 %** | – | – | – |
| 4032 | 28 d | 52,2 % | 19,4 % | 23,7 % | **15,3 %** | 65,5 % | 34,1 % | 3108,0 % |

(`–` = não medido nesse tamanho; MAE, P90 e `n_gaps` de cada célula em
`docs/backtest_results.json`. A coluna `prophet` é **histórica**: medido antes da remoção do
código — ver §4.5.)

### Conclusões

- **Não existe um vencedor universal** — por isso os tiers. Cada faixa tem um modelo claro:
  - **≤ 4 slots**: `linear` (MAE 1,7–2,3). Interpolar a reta é ótimo quando o buraco tem 15–60 min.
  - **5–192 slots**: `seasonal_residual` (MAPE ~21 %).
  - **193–1344 slots**: `weekly_naive` — repetir o mesmo slot da semana anterior (MAPE 20–33 %).
    Vence porque a série é **muito** estável semana a semana (perfil 2024×2025 corr = 0,998).
  - **> 1344 slots**: `seasonal` puro (nível × perfil), MAPE 15–29 %.
- **`linear` colapsa** com o tamanho do gap: 2,9 % → 110 % em 7 dias. A reta atravessa a curva
  diária no sentido errado. Nunca usar acima de 4 slots.
- **Prophet perde para os simples** em todos os tamanhos (28–73 %) e é ~40× mais lento
  (17 s/gap vs 0,05 s). Confirma §4.5 — fora do caminho crítico.
- **`darts.ExponentialSmoothing` é instável**: MAPE 6–230 % e explode em gaps longos (3108 %).
  Rejeitado.
- **`darts.NaiveSeasonal(K=96)`** (46 % em gap de 1 slot) perde para o `weekly_naive` que faz
  a mesma ideia com janela de 1 semana.
- Fora da faixa validada (> 4032 slots / 28 dias) o erro **não foi medido** — o `confidence`
  é limitado a 0,30 nesses slots (§4.3).

Critério de aceite (proposto, por tier):

| Tier | MAPE alvo | MAPE medido |
|---|---|---|
| 1 (≤ 1 h) | < 5 % | 2,9 % ✅ |
| 2 (1 h – 48 h) | < 25 % | 21 % ✅ |
| 3 (2–14 d) | < 35 % | 27–33 % ✅ |
| 4 (> 14 d) | < 40 % | 15–29 % ✅ |

Reproduzir: `python -m src.pipeline.backtest --output backtest_results.json`
(versão só-rápida: `--no-slow`, ~30 s).

---

## 6. Decisões

Todas resolvidas. D2–D5 foram decididas durante a implementação; **D1 foi a última, decidida
pelo usuário em 2026-10-06 → opção A (preencher)**.

### D1 — o que fazer com os 2 blocos > 7 dias? **decidido: D1b / opção A**

Os 2 blocos (2026-02-08 → 2026-06-05, ~117 d; 2026-08-08 → 2026-10-05, ~58 d) somam
**16.867 slots = 95,6 % dos faltantes, 17,4 % da série**.

Novo elemento desde a primeira versão desta proposta: **o backtest agora mede o erro nessa
escala** (§5). Preencher com o tier 4 (`seasonal`) tem MAPE **15,3 % em 28 dias** e **29,2 % em
21 dias** — melhor que linear (52–81 %), que Prophet (65 %) e que `weekly_naive` (24–57 %).
Ou seja: a opção "preencher" deixou de ser chute — *entre as alternativas testadas*, há número
medido que a favorece (a ressalva de fundo logo abaixo).

| Opção | O que faz | Contra | Erro medido |
|---|---|---|---|
| **D1a. `gap-marked`** | Slots ficam `NaN`, `source = "missing"`, série não é contínua | Você perde continuidade para forecasting | n/a (não preenche) |
| **D1b. Preencher** ⭐ *atual* | Tier 4 `seasonal`, `source = "synthetic"`, `confidence = 0,30` | 16.867 valores sintéticos; nível de 2026 difere de 2025 | MAPE 15–29 % |
| **D1c. Linear** | Reta de 117 dias entre os extremos | Inaceitável — a série oscila, a reta não | MAPE 52–110 % ❌ |
| **D1d. Prophet/STL** | Modelo pleno, `synthetic` | Não determinístico; pior erro que o sazonal simples | MAPE 58–65 % ❌ |

**Decisão (2026-10-06): manter D1b — preencher.** O `build_clean.py` preenche tudo, com
`confidence = 0,30` (por estar fora da faixa validada no backtest) e `gap_size` real em cada
slot, para quem quiser filtrar.

O critério não foi só o erro medido: para esses 2 blocos **não existe ground truth** (nunca
houve observação ali), então nenhum número de MAPE descreve o erro *naqueles* slots — o backtest
mede em blocos que temos, mascarados. O MAPE 15–29 % diz que `seasonal` é a melhor escolha
*entre as testadas*, não que o preenchimento está certo.

A opção **D1a (`gap-marked`) foi mantida como alternativa de uma linha de código** — trocar o
tier 4 por `source = "missing"`, valor `NaN`. E o `build_report.json` já lista os 16.867 slots,
além de `source`, `gap_size` e `confidence` permitirem filtrar:

```python
df[df.source == 'original']      # 79.101  — só dado real observado
df[df.gap_size <= 4032]          # 79.887  — remove os 2 blocos gigantes (16.867 slots)
df[df.confidence >= 0.5]         # 79.887  — idem, filtro equivalente (verificado)
```

As duas últimas deixam passar os 786 sintéticos de preenchimento pequeno (buracos ≤ 1 h) — que
são interpolação local, não extrapolação de meses.

O que **nunca**: D1c e D1d — ambos medidos pior que D1b.

> ⚠️ Nota honesta: 17,4 % da série sendo sintética distorce qualquer análise que não filtre por
> `source`. `df[df.source == 'original']` devolve os 79.101 pontos reais — use-o como padrão.

### D2 — observações fora do marco — **decidido: D2a**

- **D2a.** Mantém no slot do `floor`, marca `off_grid = true` ⭐ *(279 dos 295 são a única medida do slot)*
- ~~**D2b.**~~ Descarta e interpola (troca dado real por sintético)

Implementado. 291 slots com `off_grid = true` (295 observações fora do marco; tolerância de 60 s
calibrada — `off_grid` é o que ficou **> 60 s depois do floor**, 295 observações).
Nota: a atribuição é por **`floor`**, não por vizinho mais próximo — ver §1.4. Na prática dá no
mesmo porque a cron dispara na marca e só há latência para depois dela.

### D3 — onde o build roda — **decidido: D3b**

- ~~**D3a.**~~ No workflow, após `get_data.py`
- **D3b.** Só sob demanda ⭐ *(seu pedido: "o dataset limpo não roda no cron, só quando eu pedir")*
- ~~**D3c.**~~ Workflow separado

Implementado como CLI local: `python -m src.pipeline.build_clean`.
**Não** está no workflow — o cron de 15 min só coleta o bruto.

### D4 — limpeza das 73 linhas vazias do bruto — **decidido: D4a**

- **D4a.** Limpar agora, uma vez, com script versionado ⭐ *(seu pedido)*
- ~~**D4b.**~~ Manter o bruto intocável

Implementado e **executado**: `data.json` foi de 79.207 → 79.134 linhas (73 vazias removidas).
Segunda execução: `nada a remover (79134 linhas)` — idempotente.

### D5 — formato de saída — **decidido: D5a**

- **D5a.** Parquet + CSV ⭐
- ~~**D5b.**~~ Só CSV
- ~~**D5c.**~~ JSON de verdade

Implementado: `data/processed/occupancy_clean.{parquet,csv}` + `build_report.json`.

---

## 7. Plano de implementação

| # | Tarefa | Status |
|---|---|---|
| 1 | Separar `fetch_count()` / `append_sample()` no `get_data.py` + teste | ⬜ pendente (§3.2) |
| 2 | `src/pipeline/build_clean.py`: estágios A–D | ✅ feito |
| 3 | `src/pipeline/backtest.py`: mascarar e medir | ✅ feito (§5) |
| 4 | Limpeza pontual das 73 linhas (D4) | ✅ feito e executado |
| 5 | Wire-up no workflow + `git diff --cached --quiet` | ✅ feito (commit `ead03745d`) |
| 6 | Corrigir `pd.read_csv('data.json')` → `header=None, names=[...]` nos notebooks 02/03 | ✅ feito |
| 7 | Testes: parse, grade, proveniência, determinismo | ✅ `tests/test_pipeline.py` (12 testes) |
| 8 | `src/pipeline/clean_raw.py`: limpeza idempotente do bruto | ✅ feito e executado |
| 9 | Ruído isolado → `NaN` + `suspect_values[]` (§4.2.6) | ✅ feito (16 valores) |
| 10 | Remover Prophet (código) e enxugar `requirements.txt` | ✅ feito |

**`requirements.txt` enxuto** — só o que o código rastreado usa:

| pacote | quem usa |
|---|---|
| `requests` | `get_data.py` (cron a cada 15 min) |
| `aiohttp` | `get_url.py` (`shot.yml`) |
| `pandas`, `numpy`, `pyarrow` | `src/pipeline/` (parse, tiers, escrita parquet) |
| `pytest` | `tests/` |

Removidos `prophet`, `selenium`, `playwright`, `scikit-learn`, `xgboost`, `lightgbm`,
`matplotlib`, `seaborn`, `plotly`, `loguru`, `tqdm`, `holidays`, `beautifulsoup4` — nenhum é
importado por arquivo rastreado. Instalação limpa caiu de minutos para **10 s**, o que também
acelera o cron. Validado em venv novo: `get_data.py` exit 0, 12 testes passam, build completa.
(`darts` continua opcional e fora do requirements — é só ferramenta de avaliação, importada sob
`try/except`.)

**Critério de aceite global — verificado:**

- ✅ Rodar o build duas vezes → arquivos **byte-a-byte idênticos** (`md5` igual).
- ✅ `df[df.source == 'original']` reproduz exatamente o bruto: **79.117 slots observados no
  `data.json`, menos os 16 `suspect_values` = 79.101 `original`**. Verificado por
  `floor(bruto)` → mediana por slot → comparação item a item com o CSV: **79.101 / 79.101
  valores idênticos, 0 divergências**. (Os 16 que faltam são justamente os suspeitos, que viram
  `NaN` e depois `synthetic` — listados em `build_report.json`.)
- ✅ Zero `NaN` em `value`; `ts` monotônico, sem duplicatas.
- ✅ 0 linhas rejeitadas no parse (bruto já limpo).

---

## 8. Resumo executivo

1. **As 73 linhas vazias foram removidas** por `src/pipeline/clean_raw.py` (idempotente — a
   segunda execução reporta `nada a remover`). A causa raiz do lixo de erro
   (`str(response.json())`) já tinha sido corrigida em junho/2026.
2. **18,2 % dos slots de 15 min não têm dado**, mas **95,6 % disso está em 2 blocos** (117 e 58 dias).
   Os faltantes "normais" são só 0,8 % da série.
3. **A sazonalidade é dupla e estável** (corr 0,998 entre 2024→2025) — e o backtest confirmou que
   modelos simples batem Prophet e Darts: `linear` em gaps ≤ 1 h, `seasonal_residual` até 48 h,
   `weekly_naive` até 14 dias, `seasonal` acima disso.
4. **A informação de proveniência (`source`, `method`, `gap_id`, `gap_size`, `off_grid`,
   `n_obs`, `confidence`) vem embutida** — nenhum consumidor downstream é obrigado a saber que
   existe interpolação. `df[df.source == 'original']` devolve a série bruta intacta.
5. **16 valores de ruído isolado** (zeros entre vizinhos de 25–246) viraram `NaN` e são listados
   em `build_report.json`. O detector é conservador de propósito: **não** toca em eventos reais
   (a noite de carnaval com 78 slots acima de 10×MAD é dado, não artefato).
6. **D1 — a última decisão — foi fechada: preencher (opção A).** Os 17,4 % da série nos 2 blocos
   gigantes ficam com tier 4, `confidence = 0,30`, e são 100 % filtráveis por `source`,
   `gap_size` ou `confidence`. A alternativa `gap-marked` continua a uma linha de código. Ver §6.
