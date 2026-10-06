"""Testes da coleta: obter -> validar -> persistir (docs 3.2).

Garante que o caminho de escrita nao tem como produzir linha malformada.
Importar `get_data` nao faz requisicao nenhuma -- senao estes testes dariam
timeout de rede.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import get_data  # noqa: E402
from get_data import DATA_PATH, MAX_RETRIES, append_sample, fetch_count, parse_count  # noqa: E402
from src.pipeline.build_clean import LINE_RE  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise RuntimeError(self._status_error)

    def json(self):
        return self._payload


class _FakeSession:
    """Devolve uma sequencia de respostas; repete a ultima se a lista acabar."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        i = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[i]


def _ok(contagem="61"):
    return _FakeResponse({"success": True, "data": {"contagem": contagem, "hora": "15:10"}})


# ---------------------------------------------------------------------------
# parse_count
# ---------------------------------------------------------------------------
def test_parse_count_valid():
    assert parse_count({"success": True, "data": {"contagem": "61", "hora": "15:10"}}) == 61


def test_parse_count_accepts_int_already():
    assert parse_count({"success": True, "data": {"contagem": 61}}) == 61


def test_parse_count_rejects_zero():
    # 0 = sentinela de falha do endpoint antigo (ver MIN_VALID_COUNT)
    assert parse_count({"success": True, "data": {"contagem": "0"}}) is None


def test_parse_count_rejects_non_numeric():
    assert parse_count({"success": True, "data": {"contagem": "abc"}}) is None
    assert parse_count({"success": True, "data": {"contagem": None}}) is None
    assert parse_count({"success": True, "data": {"contagem": ""}}) is None


def test_parse_count_rejects_success_false():
    assert parse_count({"success": False, "data": {"contagem": "61"}}) is None


def test_parse_count_rejects_missing_data():
    assert parse_count({"success": True}) is None
    assert parse_count({"success": True, "data": None}) is None
    assert parse_count({"success": True, "data": {}}) is None


def test_parse_count_rejects_non_dict():
    assert parse_count(None) is None
    assert parse_count("61") is None
    assert parse_count(61) is None


# ---------------------------------------------------------------------------
# fetch_count
# ---------------------------------------------------------------------------
def test_fetch_count_success_first_try():
    session = _FakeSession([_ok("61")])
    sleeps = []
    assert fetch_count(session=session, sleep=sleeps.append) == 61
    assert session.calls == 1
    assert sleeps == []  # nao dorme entre tentativas


def test_fetch_count_retries_then_succeeds():
    session = _FakeSession([
        _FakeResponse(status_error="500"),
        _FakeResponse({"success": False}),
        _ok("55"),
    ])
    sleeps = []
    assert fetch_count(session=session, sleep=sleeps.append) == 55
    assert session.calls == 3
    assert len(sleeps) == 2  # dormiu entre as 3 tentativas


def test_fetch_count_raises_after_all_retries():
    session = _FakeSession([_FakeResponse(status_error="boom")])
    sleeps = []
    with pytest.raises(RuntimeError, match="retries exhausted"):
        fetch_count(session=session, sleep=sleeps.append)
    assert session.calls == MAX_RETRIES
    assert len(sleeps) == MAX_RETRIES - 1


def test_fetch_count_raises_when_payload_always_invalid():
    # resposta HTTP 200 mas payload ruim: nada e retornado como int
    session = _FakeSession([_FakeResponse({"success": True, "data": {"contagem": "0"}})])
    with pytest.raises(RuntimeError):
        fetch_count(session=session, sleep=lambda s: None)


# ---------------------------------------------------------------------------
# append_sample — o caminho de escrita nao pode produzir linha malformada
# ---------------------------------------------------------------------------
def test_append_sample_writes_valid_line(tmp_path):
    f = tmp_path / "data.json"
    line = append_sample(f, 12)
    # a linha gravada casa o mesmo regex que o pipeline usa no parse
    assert LINE_RE.match(line) is not None
    assert line.endswith(",12")
    assert f.read_text() == line + "\n"


def test_append_sample_accepts_zero_value(tmp_path):
    # 0 e valido AQUI: quem filtra 0 e o fetch (MIN_VALID_COUNT), nao a escrita
    f = tmp_path / "data.json"
    line = append_sample(f, 0)
    assert LINE_RE.match(line) is not None
    assert line.endswith(",0")


def test_append_sample_dict_raises_typeerror_and_writes_nothing(tmp_path):
    f = tmp_path / "data.json"
    with pytest.raises(TypeError):
        append_sample(f, {"contagem": 61})
    assert not f.exists()  # nem o arquivo foi criado


def test_append_sample_typeerror_keeps_existing_file_intact(tmp_path):
    f = tmp_path / "data.json"
    f.write_text("2024-01-02 16:00:00,57\n")
    before = f.read_text()
    for bad in [{"a": 1}, [61], "61", 61.0, None]:
        with pytest.raises(TypeError):
            append_sample(f, bad)
    assert f.read_text() == before


def test_append_sample_bool_raises_typeerror(tmp_path):
    # True e subclasse de int e formataria como "True" -> linha invalida
    f = tmp_path / "data.json"
    with pytest.raises(TypeError):
        append_sample(f, True)
    assert not f.exists()


def test_append_sample_negative_raises_valueerror(tmp_path):
    f = tmp_path / "data.json"
    with pytest.raises(ValueError, match="negativa"):
        append_sample(f, -1)
    assert not f.exists()


def test_append_sample_terminates_missing_final_newline(tmp_path):
    f = tmp_path / "data.json"
    f.write_text("2024-01-02 16:00:00,57")  # sem \n no fim
    line = append_sample(f, 58)
    assert f.read_text() == f"2024-01-02 16:00:00,57\n{line}\n"


def test_append_sample_appends_to_existing_file(tmp_path):
    f = tmp_path / "data.json"
    f.write_text("2024-01-02 16:00:00,57\n")
    line = append_sample(f, 58)
    assert f.read_text() == f"2024-01-02 16:00:00,57\n{line}\n"


def test_append_sample_uses_injected_timestamp(tmp_path):
    import datetime
    from zoneinfo import ZoneInfo

    f = tmp_path / "data.json"
    now = datetime.datetime(2026, 10, 6, 12, 0, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert append_sample(f, 58, now=now) == "2026-10-06 12:00:00,58"


def test_append_sample_rejects_string_path_type_confusion(tmp_path):
    # garante que so int passa -- nada que formatasse errado escapa
    f = tmp_path / "data.json"
    with pytest.raises(TypeError):
        append_sample(f, "58")
    assert not f.exists()


# ---------------------------------------------------------------------------
# contrato do modulo
# ---------------------------------------------------------------------------
def test_importing_get_data_performs_no_network(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - so roda se o import fizer rede
        raise AssertionError("importar get_data nao pode fazer requisicao")

    monkeypatch.setattr(get_data.requests, "get", _boom)
    import importlib

    importlib.reload(get_data)  # recarrega sem executar main()


def test_data_path_is_relative_to_cwd():
    # a action roda a partir da raiz do repo; manter relativo
    assert DATA_PATH == "data.json"
    assert re.fullmatch(r"[\w./-]+\.json", DATA_PATH)


# ---------------------------------------------------------------------------
# main() — exit codes sao o que faz a action falhar visivelmente
# ---------------------------------------------------------------------------
def test_main_returns_zero_and_appends(monkeypatch, tmp_path):
    out = tmp_path / "data.json"
    monkeypatch.setattr(get_data, "DATA_PATH", str(out))
    monkeypatch.setattr(get_data, "fetch_count", lambda: 61)
    assert get_data.main() == 0
    assert LINE_RE.match(out.read_text().rstrip("\n")) is not None


def test_main_returns_one_when_fetch_fails(monkeypatch, tmp_path):
    out = tmp_path / "data.json"

    def _fail():
        raise RuntimeError("all 5 retries exhausted: 404")

    monkeypatch.setattr(get_data, "DATA_PATH", str(out))
    monkeypatch.setattr(get_data, "fetch_count", _fail)
    assert get_data.main() == 1
    assert not out.exists()  # nada gravado quando a coleta falha
