"""Build do dataset limpo a partir do bruto.

Pipeline determinístico e idempotente:
  1. Parse tolerante do bruto (rejeita linhas invalidas)
  2. Consolida na grade de 15 min (off-grid -> slot mais proximo, duplicatas -> mediana)
  3. Reindexa para grade completa
  4. Interpola gaps por tamanho (linear / sazonal / sazonal+residuo)
  5. Marca proveniencia: source, method, gap_id, confidence

Uso:
    python -m src.pipeline.build_clean [--input data.json] [--output-dir data/processed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+)$")
TZ = "America/Sao_Paulo"
FREQ = "15min"

# Tiers calibrados pelo backtest (docs/DATA_PIPELINE_PROPOSAL.md §5).
# confidence = 1 - MAPE medido na faixa do tier.
TIERS: list[tuple[int, str]] = [
    (4, "linear"),             # MAPE 2.9%
    (192, "seasonal_residual"),  # MAPE 21%
    (1344, "weekly_naive"),    # MAPE 33%
    (float("inf"), "seasonal"),  # MAPE 29%
]
CONFIDENCE: dict[str, float] = {
    "linear": 0.97,
    "seasonal_residual": 0.79,
    "weekly_naive": 0.67,
    "seasonal": 0.71,
}
# maior gap validado no backtest (4032 slots = 28 dias); acima disso confidence
# e limitado porque o erro nao foi medido nessa escala
MAX_BACKTESTED_GAP = 4032


# ---------------------------------------------------------------------------
# Stage A: parse tolerante
# ---------------------------------------------------------------------------

def parse_raw(path: str) -> tuple[pd.DataFrame, list[dict]]:
    """Retorna (df com ts/value, lista de linhas rejeitadas)."""
    rows = []
    rejected = []
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        l = line.rstrip("\n")
        m = LINE_RE.match(l)
        if m:
            ts = pd.Timestamp(m.group(1), tz=TZ)
            rows.append((ts, int(m.group(2))))
        else:
            rejected.append({"line": i, "content": l[:200], "reason": "invalid_format"})
    df = pd.DataFrame(rows, columns=["ts", "value"]).set_index("ts")
    return df, rejected


# ---------------------------------------------------------------------------
# Stage B: consolidar na grade de 15 min
# ---------------------------------------------------------------------------

def detect_noise(agg: pd.DataFrame) -> tuple[pd.Series, list[dict]]:
    """Marca artefatos isolados de sensor (proposta §4.2.6).

    Conservador de proposito: so marca o que e inequivocamente ruido
    tecnico, nunca eventos reais (noite de carnaval tem 78 slots seguidos
    acima de 10xMAD -- isso e dado, nao artefato).

      - zero isolado: value == 0 com um vizinho observado > 5
      - spike isolado: > 10xMAD da celula (weekday, slot), com os DOIS
        vizinhos imediatos dentro de 3xMAD (nao sao parte do mesmo evento)

    Slot sem dado vizinho (NaN) conta como desconhecido -> nao marca.
    Retorna (mascara, lista de suspeitos para o relatorio).
    """
    v = agg["value"]
    suspect = pd.Series(False, index=v.index)
    details: list[dict] = []

    obs = v.dropna()
    if len(obs) < 96 * 7:
        return suspect, details

    cell = pd.DataFrame({"v": obs.values}, index=obs.index)
    cell["wd"] = cell.index.weekday
    cell["sl"] = cell.index.hour * 4 + cell.index.minute // 15
    cell_med = cell.groupby(["wd", "sl"])["v"].transform("median")
    dev = (cell["v"] - cell_med).abs()
    mad = dev.groupby([cell["wd"], cell["sl"]]).transform("median")
    global_mad = float(dev.median())
    # piso de 1.0: evita divisao por zero em celulas constantes. Na serie
    # real o MAD global e 27, entao o piso nao altera nada.
    scale = np.maximum(np.maximum(mad, global_mad), 1.0)
    z = (dev / scale).reindex(v.index)

    # 1. spikes isolados
    neighbor_ok = (z.shift(1).fillna(99) <= 3) & (z.shift(-1).fillna(99) <= 3)
    spike = (z > 10) & neighbor_ok

    # 2. zeros isolados
    zero = v.eq(0) & ((v.shift(1).fillna(0) > 5) | (v.shift(-1).fillna(0) > 5))

    mask = (spike | zero).fillna(False)
    # mediana da celula, alinhada ao indice completo (slots vazios -> NaN)
    cell_med_full = cell_med.reindex(v.index)
    for ts in v.index[mask]:
        i = v.index.get_loc(ts)
        prev_v = v.iloc[i - 1] if i > 0 else np.nan
        next_v = v.iloc[i + 1] if i < len(v) - 1 else np.nan
        # desvio pra cima ou pra baixo da celula
        med_v = cell_med_full.loc[ts]
        if v.loc[ts] == 0:
            reason = "isolated_zero"
        else:
            direction = "above" if float(v.loc[ts]) > float(med_v) else "below"
            reason = f"isolated_outlier_{direction}"
        details.append({
            "ts": str(ts),
            "value": float(v.loc[ts]),
            "prev": None if pd.isna(prev_v) else float(prev_v),
            "next": None if pd.isna(next_v) else float(next_v),
            "reason": reason,
        })
    return mask, details


def consolidate(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Alinha na grade de 15 min, trata off-grid e duplicatas."""
    if len(df) == 0:
        raise ValueError("consolidate: dataframe vazio (nenhuma linha valida)")

    # alinha ao slot (floor)
    df = df.copy()
    df["slot"] = df.index.floor(FREQ)

    # marca off-grid (mais de 60s do inicio do slot)
    off_grid_delta = pd.Series(df.index, index=df.index) - df["slot"]
    df["off_grid"] = off_grid_delta.dt.total_seconds() > 60

    # duplicatas: mediana por slot
    agg = df.groupby("slot").agg(
        value=("value", "median"),
        n_obs=("value", "count"),
        off_grid=("off_grid", "any"),
    )
    agg["value"] = agg["value"].astype(float)

    # reindexa para grade completa
    full_idx = pd.date_range(agg.index[0], agg.index[-1], freq=FREQ, tz=TZ)
    agg = agg.reindex(full_idx)
    agg.index.name = "ts"

    stats = {
        "n_raw": int(len(df)),
        "n_slots": int(len(agg)),
        "n_original": int(agg["value"].notna().sum()),
        "n_off_grid": int(agg["off_grid"].sum()),
        "n_duplicates": int((agg["n_obs"] > 1).sum()),
    }
    return agg, stats


# ---------------------------------------------------------------------------
# Stage C: interpolacao
# ---------------------------------------------------------------------------

def seasonal_profile(series: pd.Series) -> pd.Series:
    """Perfil sazonal multiplicativo: mediana por (weekday, slot) / mediana global.

    Garante as 7*96 celulas: celulas sem dado nenhum recebem 1.0 (valor
    neutro), para nao propagar NaN na predicao.
    """
    df = series.dropna().to_frame("v")
    df["weekday"] = df.index.weekday
    df["slot"] = df.index.hour * 4 + df.index.minute // 15
    cell_med = df.groupby(["weekday", "slot"])["v"].median()
    global_med = df["v"].median()
    full_idx = pd.MultiIndex.from_product([range(7), range(96)],
                                          names=["weekday", "slot"])
    profile = cell_med.reindex(full_idx) / global_med
    return profile.fillna(1.0)


def predict_seasonal(series: pd.Series, profile: pd.Series) -> pd.Series:
    """Modelo sazonal multiplicativo com nivel local.

    Se a janela do rolling cai toda dentro de um gap (nivel = NaN), faz
    fallback para a mediana global, para nao propagar NaN.
    """
    df = series.to_frame("v")
    df["weekday"] = df.index.weekday
    df["slot"] = df.index.hour * 4 + df.index.minute // 15
    df["profile"] = df.set_index(["weekday", "slot"]).index.map(profile)
    df["level"] = df["v"].rolling(96 * 7, center=True, min_periods=1).median()
    df["level"] = df["level"].fillna(df["v"].median())
    return df["level"] * df["profile"]


def weekly_naive(series: pd.Series) -> pd.Series | None:
    """Mesmo slot da semana anterior, andando ate 8 semanas atras.

    Retorna None se nenhum slot NaN tiver correspondente observado
    (ex.: gap maior que 8 semanas).
    """
    out = pd.Series(np.nan, index=series.index)
    filled = 0
    for ts in series.index[series.isna()]:
        for k in range(1, 9):
            prev = ts - pd.Timedelta(weeks=k)
            v = series.get(prev)
            if v is not None and not pd.isna(v):
                out.loc[ts] = v
                filled += 1
                break
    return out if filled else None


def interpolate(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Retorna (valores, source, method, gap_id, gap_size, confidence).

    Tiers calibrados pelo backtest (src/pipeline/backtest.py, ver
    docs/DATA_PIPELINE_PROPOSAL.md §5):
      - gap <=   4 slots  -> linear              (MAPE ~2.9%)
      - gap <= 192 slots  -> seasonal_residual   (MAPE ~21%)
      - gap <= 1344 slots -> weekly_naive        (MAPE ~33%)
      - gap >  1344 slots -> seasonal            (MAPE ~29%)
    """
    profile = seasonal_profile(series)
    y_hat = predict_seasonal(series, profile)
    resid = series - y_hat
    resid_interp = resid.interpolate(method="linear", limit_direction="both")
    y_seasonal_resid = y_hat + resid_interp
    y_linear = series.interpolate(method="linear", limit_direction="both")
    y_weekly = weekly_naive(series)
    if y_weekly is None:
        y_weekly = y_hat
    else:
        # slot sem correspondente semanal observada cai no sazonal puro
        y_weekly = y_weekly.fillna(y_hat)

    # identifica gaps
    is_na = series.isna()
    gap_id = pd.Series(0, index=series.index, dtype=int)
    gap_size = pd.Series(0, index=series.index, dtype=int)
    current_id = 0
    current_size = 0
    for i, (ts, na) in enumerate(is_na.items()):
        if na:
            if current_size == 0:
                current_id += 1
            current_size += 1
            gap_id.iloc[i] = current_id
            gap_size.iloc[i] = current_size
        else:
            current_size = 0

    # decide metodo por gap (tiers calibrados pelo backtest)
    result = series.copy()
    source = pd.Series("original", index=series.index, dtype="object")
    method = pd.Series("observed", index=series.index, dtype="object")
    gap_total = pd.Series(0, index=series.index, dtype=int)
    confidence = pd.Series(1.0, index=series.index, dtype=float)

    for gid in range(1, current_id + 1):
        mask = gap_id == gid
        # gap_size incrementa ao longo do gap: o tamanho real e o MAX, nao o primeiro
        size = int(gap_size[mask].max())
        # publica o tamanho TOTAL do gap em todos os slots dele (facilita filtrar)
        gap_total.loc[mask] = size

        # primeiro tier cujo limite cobre o tamanho do gap
        chosen = next(m for limit, m in TIERS if size <= limit)
        y_pred = {"linear": y_linear,
                  "seasonal_residual": y_seasonal_resid,
                  "weekly_naive": y_weekly,
                  "seasonal": y_hat}[chosen]

        result.loc[mask] = y_pred.loc[mask]
        method.loc[mask] = chosen
        source.loc[mask] = "synthetic"
        conf = CONFIDENCE[chosen]
        if size > MAX_BACKTESTED_GAP:
            # fora da faixa validada no backtest (max 4032 slots = 28 dias)
            conf = min(conf, 0.30)
        confidence.loc[mask] = conf

    return result, source, method, gap_id, gap_total, confidence


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data.json")
    ap.add_argument("--output-dir", default="data/processed")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Stage A: parse...")
    df, rejected = parse_raw(args.input)
    print(f"  {len(df)} linhas validas, {len(rejected)} rejeitadas")

    print("Stage B: consolida...")
    agg, stats = consolidate(df)

    print("Stage B2: ruido isolado...")
    noise_mask, suspects = detect_noise(agg)
    n_noise = int(noise_mask.sum())
    if n_noise:
        agg.loc[noise_mask, "value"] = np.nan
    stats["n_suspect_values"] = n_noise
    print(f"  {n_noise} valores suspeitos viraram NaN")
    print(f"  {stats}")

    print("Stage C: interpola...")
    values, source, method, gap_id, gap_size, confidence = interpolate(agg["value"])
    print(f"  {int((source == 'synthetic').sum())} slots sinteticos")

    # monta output (slots sinteticos: sem obs original -> off_grid=False, n_obs=0)
    off_grid = agg["off_grid"].eq(True)   # NaN (slot vazio) vira False
    n_obs = agg["n_obs"].fillna(0).astype(int)
    out = pd.DataFrame({
        "ts": values.index,
        "value": values.values,
        "source": source.values,
        "method": method.values,
        "gap_id": gap_id.values,
        "gap_size": gap_size.values,
        "off_grid": off_grid.values,
        "n_obs": n_obs.values,
        "confidence": confidence.values,
    })

    # salva
    out.to_parquet(out_dir / "occupancy_clean.parquet", index=False)
    out.to_csv(out_dir / "occupancy_clean.csv", index=False)

    # relatorio
    report = {
        "input": args.input,
        "rejected_lines": rejected,
        "consolidation": stats,
        "n_synthetic": int((source == "synthetic").sum()),
        "n_original": int((source == "original").sum()),
        "suspect_values": suspects,
        "methods": method.value_counts().to_dict(),
        "tiers": [
            {"max_gap_size": (None if lim == float("inf") else lim),
             "method": m, "confidence": CONFIDENCE[m]}
            for lim, m in TIERS
        ],
        "max_backtested_gap": MAX_BACKTESTED_GAP,
    }
    (out_dir / "build_report.json").write_text(json.dumps(report, indent=2, default=str))

    print(f"\nSalvo em {out_dir}/occupancy_clean.parquet e .csv")
    print(f"Relatorio em {out_dir}/build_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
