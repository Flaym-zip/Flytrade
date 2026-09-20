#!/usr/bin/env python3
"""Read local Flytrade health for a bounded period; never send trading commands.
No third-party dependency. The only requests target the local app, not Kraken.
"""
import argparse
import datetime
import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8088')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--output', type=Path, default=Path('diagnostic-flux07.json'))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('--seconds doit etre compris entre 1 et 300')
    parsed = urlparse(args.url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username:
        parser.error('--url doit etre une adresse HTTP locale, sans identifiants')
    rows = []
    start = time.monotonic()
    try:
        while time.monotonic()-start < args.seconds:
            began = time.monotonic()
            row = {'elapsed_s': round(began-start, 3)}
            try:
                with urlopen(args.url.rstrip('/')+'/api/feed/diagnostics', timeout=2) as response:
                    d = json.load(response)
                rest = d.get('rest', {})
                row.update(provider=d.get('provider'), ready=d.get('ready'),
                           ws_age_ms=d.get('age_ms'), trade_age_ms=d.get('trade_age_ms'),
                           http_age_ms=rest.get('response_age_ms'), http_ok=rest.get('healthy'),
                           http_successes=rest.get('successes'), http_attempts=rest.get('attempts'),
                           http_failures=rest.get('failures'), max_success_gap_s=rest.get('max_success_gap_s'),
                           http_gaps_over_3s=rest.get('success_gaps_over_3s'),
                           http_p95_ms=rest.get('p95_latency_ms'), last_error=rest.get('error'),
                           status=d.get('status'))
                print('t={:5.1f}s  {}  WS={}ms  trade={}ms  HTTP={}ms  succes={}/{}'.format(
                    row['elapsed_s'], row['provider'], row['ws_age_ms'] if row['ws_age_ms'] is not None else '--', row['trade_age_ms'] if row['trade_age_ms'] is not None else '--',
                    row['http_age_ms'] if row['http_age_ms'] is not None else '--', row['http_successes'] if row['http_successes'] is not None else '--', row['http_attempts'] if row['http_attempts'] is not None else '--'), flush=True)
            except (URLError, TimeoutError, ValueError, OSError) as exc:
                row['error'] = '{}: {}'.format(type(exc).__name__, exc)
                print(row['error'], flush=True)
            rows.append(row)
            remaining = args.seconds-(time.monotonic()-start)
            if remaining > 0:
                time.sleep(min(remaining, max(0., 1-(time.monotonic()-began))))
    except KeyboardInterrupt:
        print('Interruption; sauvegarde des observations deja recueillies.')
    report = {'version': '0.7.0-alpha', 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'duration_seconds': round(time.monotonic()-start, 3),
              'note': 'Mesure locale descriptive; aucune garantie de disponibilite future. HTTP ne valide pas une touche.',
              'samples': rows}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print('Rapport :', args.output.resolve())


if __name__ == '__main__':
    main()
