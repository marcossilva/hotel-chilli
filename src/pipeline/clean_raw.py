"""Remove linhas invalidas do arquivo bruto (data.json).

Idempotente: rodar de novo nao muda nada. Nunca reescreve o arquivo se
nao houver nada a remover.

Uso:
    python -m src.pipeline.clean_raw [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+)$")


def is_valid(line: str) -> bool:
    return bool(LINE_RE.match(line.rstrip("\n")))


def clean(path: Path, dry_run: bool = False) -> tuple[int, int]:
    """Retorna (total_de_linhas, linhas_removidas)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    kept = [l for l in lines if is_valid(l)]
    removed = len(lines) - len(kept)

    if removed and not dry_run:
        path.write_text("".join(kept), encoding="utf-8")
    return len(lines), removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    total, removed = clean(path, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[dry-run] {total} linhas, {removed} seriam removidas")
    elif removed:
        print(f"removidas {removed} linhas de {total} ({path})")
    else:
        print(f"nada a remover ({total} linhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
