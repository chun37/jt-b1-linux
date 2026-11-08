#!/usr/bin/env python3
"""Index a reader-provided SKK dictionary without evaluating Lisp forms."""
import argparse
import json
import os
import re
import sqlite3
from pathlib import Path


def build(source, target):
    # Exclusive creation: never delete an existing dictionary or follow symlinks.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(target) as connection:
            connection.execute('CREATE TABLE words(reading TEXT PRIMARY KEY, candidates TEXT NOT NULL) WITHOUT ROWID')
            count = 0
            with source.open(encoding='euc_jp') as stream:
                for line in stream:
                    if line.startswith(';') or ' /' not in line:
                        continue
                    reading, values = line.strip().split(' ', 1)
                    if not re.fullmatch('[ぁ-ゖー]+', reading):
                        continue
                    candidates = []
                    for item in values.strip('/').split('/'):
                        item = item.split(';', 1)[0]
                        if (item and not item.startswith(('(', '[')) and len(item) < 80
                                and all(c.isprintable() for c in item) and item not in candidates):
                            candidates.append(item)
                    if candidates:
                        connection.execute('INSERT OR REPLACE INTO words VALUES (?, ?)',
                                           (reading, json.dumps(candidates[:60], ensure_ascii=False)))
                        count += 1
            connection.commit()
            connection.execute('VACUUM')
    except BaseException:
        target.unlink()
        raise
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='SKK-JISYO.L (EUC-JP), obtained separately')
    parser.add_argument('output', type=Path, help='new SQLite output; existing files are refused')
    args = parser.parse_args()
    print('Indexed kana readings:', build(args.source, args.output))


if __name__ == '__main__':
    main()
