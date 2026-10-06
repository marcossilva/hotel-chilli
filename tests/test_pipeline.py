"""Testes do pipeline de limpeza e build do dataset limpo."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline.build_clean import (  # noqa: E402
    LINE_RE,
    consolidate,
    interpolate,
    seasonal_profile,
)
from src.pipeline.clean_raw import clean, is_valid  # noqa: E402


# ---------------------------------------------------------------------------
# clean_raw
# ---------------------------------------------------------------------------
def test_line_re_accepts_valid():
    m = LINE_RE.match("2024-01-02 16:00:00,57")
    assert m is not None
    assert m.group(2) == "57"


def test_line_re_rejects_error_line():
    assert LINE_RE.match("{'error': {'code': '503'}, 'ts': '2024-01-01'}") is None


def test_line_re_rejects_empty():
    assert LINE_RE.match("") is None


def test_is_valid():
    assert is_valid("2024-01-02 16:00:00,57\n")
    assert not is_valid("\n")
    assert not is_valid("{'error': {'code': '503'}}\n")


def test_clean_file_idempotent(tmp_path):
    f = tmp_path / "data.json"
    f.write_text(
        "2024-01-02 16:00:00,57\n"
        "\n"
        "{'error': {'code': '503'}}\n"
        "2024-01-02 16:15:00,58\n"
    )
    n1, r1 = clean(f, dry_run=False)
    assert n1 == 4 and r1 == 2  # remove 1 vazia + 1 erro

    n2, r2 = clean(f, dry_run=False)
    assert n2 == 2 and r2 == 0  # idempotente
    assert f.read_text() == "2024-01-02 16:00:00,57\n2024-01-02 16:15:00,58\n"


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------
def _mk_df(rows):
    if not rows:
        return pd.DataFrame({"ts": pd.DatetimeIndex([]), "value": []}).set_index("ts")
    idx = pd.to_datetime([r[0] for r in rows]).tz_localize("America/Sao_Paulo")
    return pd.DataFrame(
        {"ts": idx, "value": [r[1] for r in rows]}
    ).set_index("ts")


def test_consolidate_grid_and_offgrid():
    df = _mk_df(
        [
            ("2024-01-02 16:00:00", 50),
            ("2024-01-02 16:01:30", 60),   # off-grid (90s), mesmo slot -> media
            ("2024-01-02 16:15:00", 55),
        ]
    )
    agg, stats = consolidate(df)
    assert stats["n_slots"] == 2
    assert stats["n_original"] == 2
    assert agg["value"].iloc[0] == 55          # media de 50 e 60
    assert bool(agg["off_grid"].iloc[0]) is True
    assert bool(agg["off_grid"].iloc[1]) is False
    assert stats["n_duplicates"] == 1


def test_consolidate_empty_index_error():
    with pytest.raises(ValueError):
        consolidate(_mk_df([]))


# ---------------------------------------------------------------------------
# interpolate
# ---------------------------------------------------------------------------
def test_interpolate_tiers():
    # serie sintetica com um gap curto (2), um medio (10) e um gigante (1400)
    idx = pd.date_range("2024-01-01", periods=3000, freq="15min",
                         tz="America/Sao_Paulo")
    series = pd.Series(
        [50 + 20 * ((i % 96) / 96) + (i // 96) % 7 for i in range(len(idx))],
        index=idx,
        dtype=float,
    )
    series.iloc[10:12] = np_nan()    # gap 2 slots -> linear
    series.iloc[200:210] = np_nan()  # gap 10 slots -> sazonal
    series.iloc[1000:2400] = np_nan()  # gap 1400 slots -> sazonal puro

    result, source, method, gap_id, gap_total, confidence = interpolate(series)

    assert source.iloc[10] == "synthetic"
    assert method.iloc[10] == "linear"
    assert gap_total.iloc[10] == 2

    assert method.iloc[200] == "seasonal_residual"
    assert gap_total.iloc[200] == 10

    # gap > 1344 slots -> sazonal puro
    assert method.iloc[1000] == "seasonal"
    assert gap_total.iloc[1000] == 1400

    # originais intactos
    assert source.iloc[50] == "original"
    assert result.iloc[50] == series.iloc[50]
    assert confidence.iloc[50] == 1.0
    # sem NaN
    assert result.isna().sum() == 0


def np_nan():
    import numpy as np
    return np.nan


def test_detect_noise_flags_isolated_not_events():
    """So marca artefatos isolados; evento real (blocos longos) fica."""
    from src.pipeline.build_clean import detect_noise

    idx = pd.date_range("2024-01-01", periods=96 * 10, freq="15min",
                        tz="America/Sao_Paulo")
    vals = pd.Series(80.0, index=idx)
    vals.iloc[100] = 0.0        # zero isolado entre vizinhos 80 -> marca
    vals.iloc[500:578] = 300.0  # "evento" de 78 slots -> NAO marca

    agg = pd.DataFrame({"value": vals})
    mask, details = detect_noise(agg)

    assert int(mask.sum()) == 1
    assert mask.iloc[100] is True or mask.iloc[100] == True  # noqa: E712
    assert not mask.iloc[500:578].any()
    assert len(details) == 1
    assert details[0]["reason"] == "isolated_zero"
    assert details[0]["value"] == 0.0
    assert details[0]["prev"] == 80.0


def test_seasonal_profile_no_nan():
    idx = pd.date_range("2024-01-01", periods=672, freq="15min")
    series = pd.Series(range(672), index=idx, dtype=float)
    prof = seasonal_profile(series)
    assert len(prof) == 96 * 7
    assert prof.isna().sum() == 0


def test_predict_seasonal_fallback_no_nan():
    """Gap de 7 dias nao pode propagar NaN (nivel do rolling = NaN)."""
    from src.pipeline.build_clean import predict_seasonal
    idx = pd.date_range("2024-01-01", periods=672 + 200, freq="15min")
    series = pd.Series(range(len(idx)), index=idx, dtype=float)
    series.iloc[100:772] = float("nan")  # gap de 672 slots
    prof = seasonal_profile(series)
    y_hat = predict_seasonal(series, prof)
    assert y_hat.isna().sum() == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_build_cli(tmp_path):
    data = tmp_path / "data.json"
    data.write_text(
        "\n".join(
            f"2024-01-{d:02d} {h:02d}:{m:02d}:00,{50 + (h % 5)}"
            for d in range(1, 4)
            for h in range(24)
            for m in (0, 15, 30, 45)
        )
        + "\n"
    )
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, "-m", "src.pipeline.build_clean",
         "--input", str(data), "--output-dir", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, r.stderr
    assert (out / "occupancy_clean.parquet").exists()
    assert (out / "build_report.json").exists()
