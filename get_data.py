"""Coleta o contador de ocupacao do Hotel Chilli e appenda uma amostra em data.json.

Fluxo separado em obter -> validar -> persistir (docs/DATA_PIPELINE_PROPOSAL.md 3.2):

    fetch_count()     rede + retry; devolve int ou levanta RuntimeError
    append_sample()   valida o tipo ANTES de abrir o arquivo; so aceita int

Separar os tres garante que o caminho de escrita nao tem como produzir linha
malformada: qualquer valor que nao seja int (ou que seja negativo) levanta
excecao antes de tocar no arquivo. Hoje a validacao vive embutida no loop de
rede -- um refator futuro poderia reabrir a brecha.

Importar este modulo nao faz nenhuma requisicao: tudo roda em main().

Uso: python3 get_data.py   (exit 0 = amostra gravada, exit 1 = nada gravado)
"""
from __future__ import annotations

import datetime
import sys
import time
from zoneinfo import ZoneInfo

import requests

MAX_RETRIES = 5
RETRY_DELAY = 10  # seconds between retries
TIMEOUT = 15      # seconds per request

URL = "https://hotelchilli.com.br/wp-admin/admin-ajax.php"
PARAMS = {"action": "atualizar_contador_chilli"}
DATA_PATH = "data.json"
TZ = "America/Sao_Paulo"

# 0 e rejeitado de proposito: o endpoint antigo devolvia 0 quando falhava
# (commit 53c5bbf93, "Fix zero/concat bugs"), entao 0 virou sentinela de erro
# e nao ocupacao. 46 zeros de 2025-03..2026-01 no bruto sao dessa epoca.
# Se o endpoint novo garantir que 0 = "hotel vazio", bastar trocar por 0.
MIN_VALID_COUNT = 1

COOKIES = {
    '_ga': 'GA1.1.1733788075.1738855457',
    '_ga_LJELMPC45K': 'GS1.1.1741443001.2.1.1741443155.60.0.961606891',
}

HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,es;q=0.6',
    'cache-control': 'no-cache',
    'dnt': '1',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://hotelchilli.com.br/',
    'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
}


# ---------------------------------------------------------------------------
# obter
# ---------------------------------------------------------------------------
def parse_count(payload) -> int | None:
    """Extrai o inteiro do payload da API. None se ausente, nao numerico ou 0.

    Payload esperado: {"success": true, "data": {"contagem": "61", "hora": "15:10"}}
    """
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    raw = (payload.get("data") or {}).get("contagem")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    return count if count >= MIN_VALID_COUNT else None


def fetch_count(session=None, sleep=time.sleep) -> int:
    """Obtem o contador com retry. Devolve int, ou levanta RuntimeError.

    session: qualquer objeto com .get(...) igual ao requests (para teste).
    sleep:   injetavel para os testes nao esperarem RETRY_DELAY segundos.
    """
    session = requests if session is None else session
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                URL,
                params=PARAMS,
                cookies=COOKIES,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            count = parse_count(payload)
            if count is not None:
                return count
            last_error = f"invalid response ({payload!r})"
        except Exception as e:  # noqa: BLE001 - retry em qualquer erro de rede/parse
            last_error = str(e)
        print(f"Attempt {attempt}/{MAX_RETRIES}: {last_error}, retrying...")
        if attempt < MAX_RETRIES:
            sleep(RETRY_DELAY)
    raise RuntimeError(f"all {MAX_RETRIES} retries exhausted: {last_error}")


# ---------------------------------------------------------------------------
# persistir
# ---------------------------------------------------------------------------
def append_sample(path, value, now=None) -> str:
    """Valida `value` e appenda `AAAA-MM-DD HH:MM:SS,valor\\n` em `path`.

    Devolve a linha gravada (sem \\n).

    Toda validacao acontece ANTES de abrir o arquivo -- valor invalido => excecao
    e arquivo intacto, entao nao existe estado em que uma linha malformada seja
    escrita. bool e rejeitado porque True e subclasse de int e formataria como
    "True"; negativo porque nao casa o regex de parse (\\d+).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"amostra deve ser int, recebido {type(value).__name__}: {value!r}")
    if value < 0:
        raise ValueError(f"amostra nao pode ser negativa: {value}")

    stamp = now or datetime.datetime.now(ZoneInfo(TZ))
    line = stamp.strftime("%Y-%m-%d %H:%M:%S") + "," + str(value) + "\n"

    with open(path, "a+") as f:
        f.seek(0, 2)
        if f.tell() > 0:
            f.seek(f.tell() - 1)
            if f.read(1) != "\n":
                f.write("\n")
        f.write(line)
    return line.rstrip("\n")


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def main() -> int:
    try:
        count = fetch_count()
    except RuntimeError as e:
        print(f"All retries exhausted, skipping. ({e})")
        return 1
    append_sample(DATA_PATH, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
