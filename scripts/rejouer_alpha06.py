#!/usr/bin/env python3
"""Compare fresh 1024/2048 models on a JSONL without touching live data."""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.academy06 import Academy06, Config06


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observations', type=Path)
    parser.add_argument('--output', type=Path, default=Path('controle-alpha06.json'))
    parser.add_argument('--price-only', action='store_true', help='No order book in inputs')
    args = parser.parse_args()
    if not args.observations.is_file():
        parser.error('Fichier JSONL introuvable')
    if args.output.exists():
        parser.error('La sortie existe deja : choisir un autre chemin')
    result = {}
    for size in (1024, 2048):
        with tempfile.TemporaryDirectory(prefix='flytrade-control-') as directory:
            academy = Academy06(Path(directory))
            try:
                with args.observations.open('rb') as source:
                    imported = academy.import_lines(source, origin=args.observations.name)
                academy.create(Config06(n_kc=size, seed=42, mode='chronological', epochs=1,
                                        shuffle_train=False, use_liquidity=not args.price_only))
                while academy.position < len(academy.plan):
                    academy.step_batch(40)
                academy.open_test()
                while academy.position < len(academy.plan):
                    academy.step_batch(40)
                result[str(size)] = academy.report_header()
                result[str(size)]['import_control'] = imported
            finally:
                academy.close()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(args.output)


if __name__ == '__main__':
    main()
