"""Backtesting de modelos de interpolacao para a serie de ocupacao.

Mascara blocos de dados reais de tamanhos variados e mede o erro de
diferentes modelos de preenchimento.

Honestidade do backtest:
  - Nenhum modelo ve os valores do gap durante o treino.
  - Modelos de interpolacao (linear, sazonal, prophet) treinam com os dados
    FORA do gap (os dois lados) e predicem apenas a janela do gap.
  - Modelos de previsao (darts) treinam apenas com os dados ANTES do gap
    (janela de train_days) e preveem a janela inteira -- sem ver o futuro.

Uso:
    python -m src.pipeline.backtest [--output backtest_results.json]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+)$")

TZ = "America/Sao_Paulo"

# assinatura de todo modelo: (serie_mascarada, inicio_gap, fim_gap) -> series
ModelFn = Callable[[pd.Series, pd.Timestamp, pd.Timestamp], pd.Series | None]


# ---------------------------------------------------------------------------
# Preparacao da serie
# ---------------------------------------------------------------------------

def load_series(path: str = "data.json") -> pd.Series:
    """Carrega a serie temporal alinhada na grade de 15 min."""
    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.rstrip("\n"))
        if m:
            ts = pd.Timestamp(m.group(1), tz=TZ)
            rows.append((ts, int(m.group(2))))
    df = pd.DataFrame(rows, columns=["ts", "value"]).set_index("ts")
    df = df.resample("15min").median()
    return df["value"]


def seasonal_profile(series: pd.Series) -> pd.Series:
    """Perfil sazonal multiplicativo: mediana por (weekday, slot) / mediana global."""
    df = series.dropna().to_frame("v")
    df["weekday"] = df.index.weekday
    df["slot"] = df.index.hour * 4 + df.index.minute // 15
    cell_med = df.groupby(["weekday", "slot"])["v"].median()
    global_med = df["v"].median()
    full_idx = pd.MultiIndex.from_product([range(7), range(96)],
                                          names=["weekday", "slot"])
    return (cell_med.reindex(full_idx) / global_med).fillna(1.0)


def predict_seasonal(series: pd.Series, profile: pd.Series) -> pd.Series:
    """Modelo sazonal multiplicativo com nivel local (mediana movel).

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


# ---------------------------------------------------------------------------
# Modelos de interpolacao (rapidos): usam dados dos dois lados do gap
# ---------------------------------------------------------------------------

def interp_linear(series: pd.Series, start, end) -> pd.Series:
    return series.interpolate(method="linear", limit_direction="both")


def interp_seasonal(series: pd.Series, start, end) -> pd.Series:
    profile = seasonal_profile(series)
    return predict_seasonal(series, profile)


def interp_seasonal_residual(series: pd.Series, start, end) -> pd.Series:
    profile = seasonal_profile(series)
    y_hat = predict_seasonal(series, profile)
    resid = series - y_hat
    resid_interp = resid.interpolate(method="linear", limit_direction="both")
    return y_hat + resid_interp


def interp_weekly_naive(series: pd.Series, start, end) -> pd.Series:
    """Mesmo slot da semana anterior, andando ate achar valor observado."""
    out = pd.Series(np.nan, index=series.index)
    for ts in series.loc[start:end].index:
        for k in range(1, 9):  # ate 8 semanas
            prev = ts - pd.Timedelta(weeks=k)
            v = series.get(prev)
            if v is not None and not pd.isna(v):
                out.loc[ts] = v
                break
    return out


FAST_MODELS: dict[str, ModelFn] = {
    "linear": interp_linear,
    "seasonal": interp_seasonal,
    "seasonal_residual": interp_seasonal_residual,
    "weekly_naive": interp_weekly_naive,
}


# ---------------------------------------------------------------------------
# Prophet: interpolacao por gap, treino so com dados fora do gap
# ---------------------------------------------------------------------------

def prophet_predict(
    series: pd.Series, start, end,
    window_days: int = 45,
) -> pd.Series | None:
    try:
        from prophet import Prophet
    except ImportError:
        return None

    lo = start - pd.Timedelta(days=window_days)
    hi = end + pd.Timedelta(days=window_days)
    train = series.loc[lo:hi].dropna().reset_index()
    if len(train) < 500:
        return None
    train.columns = ["ds", "y"]
    train["ds"] = train["ds"].dt.tz_localize(None)

    m = Prophet(
        daily_seasonality=True,
        weekly_seasonality=True,
        yearly_seasonality=False,
        changepoint_prior_scale=0.05,
    )
    m.fit(train)

    gap_idx = series.loc[start:end].index
    future = pd.DataFrame({"ds": gap_idx.tz_localize(None)})
    yhat = m.predict(future)["yhat"].values

    out = pd.Series(np.nan, index=series.index)
    out.loc[gap_idx] = yhat
    return out


# ---------------------------------------------------------------------------
# Darts: previsao pura (so dados passados)
# ---------------------------------------------------------------------------

def darts_predict_factory(kind: str, train_days: int = 14) -> ModelFn:
    """Fabrica um modelo Darts que preve o gap a partir do passado.

    kind: "seasonal" (NaiveSeasonal K=96) ou "exp_smoothing".
    Saida: serie com o gap preenchido, ou None se impossivel.
    """

    def predict(series: pd.Series, start, end) -> pd.Series | None:
        try:
            from darts import TimeSeries
            if kind == "seasonal":
                from darts.models import NaiveSeasonal
            else:
                from darts.models import ExponentialSmoothing
        except ImportError:
            return None

        lo = start - pd.Timedelta(days=train_days)
        lo = max(lo, series.index[0])
        hist = series.loc[lo:start]
        if hist.dropna().shape[0] < 96 * 2:
            return None

        # completa os buracos naturais restantes da janela de treino
        full = pd.date_range(hist.index[0], hist.index[-1], freq="15min", tz=TZ)
        hist = hist.reindex(full).interpolate(limit_direction="both")
        if hist.isna().any():
            return None

        hist_naive = hist.copy()
        hist_naive.index = hist_naive.index.tz_localize(None)
        ts = TimeSeries.from_series(hist_naive, freq="15min")

        gap_len = len(series.loc[start:end])
        if kind == "seasonal":
            model = NaiveSeasonal(K=96)
        else:
            model = ExponentialSmoothing(seasonal_periods=96)
        model.fit(ts)
        fc = model.predict(gap_len).to_series()

        out = pd.Series(np.nan, index=series.index)
        gap_idx = series.loc[start:end].index
        vals = fc.values
        out.loc[gap_idx] = vals[:len(gap_idx)]
        return out

    return predict


SLOW_MODELS: dict[str, ModelFn] = {
    "prophet": lambda s, a, b: prophet_predict(s, a, b),
    "darts_seasonal": darts_predict_factory("seasonal"),
    "darts_exp_smoothing": darts_predict_factory("exp_smoothing"),
}


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

@dataclass
class GapResult:
    model: str
    gap_size: int
    n_gaps: int
    mae: float
    mape: float
    p90: float
    elapsed: float


def pick_gaps(
    series: pd.Series,
    gap_size: int,
    n_gaps: int,
    rng: random.Random,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    valid_idx = series.dropna().index
    candidates = list(valid_idx)
    rng.shuffle(candidates)

    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start in candidates:
        end = start + pd.Timedelta(minutes=15 * (gap_size - 1))
        if end not in series.index:
            continue
        gap_idx = series.loc[start:end].index
        if len(gap_idx) == gap_size and series.loc[gap_idx].notna().all():
            if not any(start <= g[1] and end >= g[0] for g in gaps):
                gaps.append((start, end))
        if len(gaps) >= n_gaps:
            break
    return gaps


def run_backtest(
    series: pd.Series,
    gap_sizes: list[int] = [1, 4, 96, 672, 4032],
    n_gaps: int = 20,
    n_gaps_slow: int = 5,
    seed: int = 42,
    include_slow: bool = True,
) -> list[GapResult]:
    rng = random.Random(seed)
    results: list[GapResult] = []

    models: dict[str, tuple[ModelFn, int]] = {
        name: (fn, n_gaps) for name, fn in FAST_MODELS.items()
    }
    if include_slow:
        for name, fn in SLOW_MODELS.items():
            models[name] = (fn, n_gaps_slow)

    print(f"Modelos: {', '.join(models)}", flush=True)

    for gap_size in gap_sizes:
        print(f"\n=== gap_size={gap_size} slots ({gap_size * 15} min) ===", flush=True)
        gaps = pick_gaps(series, gap_size, n_gaps, rng)
        if not gaps:
            print("  sem gaps validos")
            continue

        for name, (model_fn, n_model_gaps) in models.items():
            use_gaps = gaps[:n_model_gaps]
            t0 = time.time()
            abs_errors: list[float] = []
            pct_errors: list[float] = []
            n_used = 0
            for start, end in use_gaps:
                masked = series.copy()
                masked.loc[start:end] = np.nan
                try:
                    pred = model_fn(masked, start, end)
                except Exception as e:
                    print(f"  {name}: ERRO {type(e).__name__}: {str(e)[:100]}")
                    break
                if pred is None:
                    continue
                true_vals = series.loc[start:end].values.astype(float)
                pred_vals = pred.loc[start:end].values.astype(float)
                if np.isnan(pred_vals).any():
                    continue
                abs_errors.extend(np.abs(true_vals - pred_vals))
                pct_errors.extend(
                    np.abs(true_vals - pred_vals) / np.maximum(true_vals, 1) * 100
                )
                n_used += 1
            elapsed = time.time() - t0

            if abs_errors:
                arr = np.array(abs_errors)
                r = GapResult(
                    model=name,
                    gap_size=gap_size,
                    n_gaps=n_used,
                    mae=float(np.mean(arr)),
                    mape=float(np.mean(pct_errors)),
                    p90=float(np.percentile(arr, 90)),
                    elapsed=elapsed,
                )
                results.append(r)
                print(
                    f"  {name:20s} MAE={r.mae:6.2f} MAPE={r.mape:5.1f}% "
                    f"P90={r.p90:6.2f} ({elapsed:.1f}s, n={n_used})",
                    flush=True,
                )
            else:
                print(f"  {name:20s} sem predicoes validas", flush=True)

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="backtest_results.json")
    ap.add_argument("--gap-sizes", nargs="+", type=int,
                    default=[1, 4, 96, 672, 4032])
    ap.add_argument("--n-gaps", type=int, default=20)
    ap.add_argument("--n-gaps-slow", type=int, default=5)
    ap.add_argument("--no-slow", action="store_true", help="pula prophet e darts")
    args = ap.parse_args()

    print("Carregando serie...")
    series = load_series()
    print(f"  {len(series)} slots, {series.notna().sum()} com dados")

    results = run_backtest(
        series,
        args.gap_sizes,
        args.n_gaps,
        n_gaps_slow=args.n_gaps_slow,
        include_slow=not args.no_slow,
    )

    out = {
        "gap_sizes": args.gap_sizes,
        "n_gaps_per_size": args.n_gaps,
        "n_gaps_slow": args.n_gaps_slow,
        "results": [r.__dict__ for r in results],
    }
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"\nResultados salvos em {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
