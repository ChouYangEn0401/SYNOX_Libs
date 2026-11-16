"""交易與批次的 id：前綴＋ULID（26 個 Crockford base32 字元，前 10 個是毫秒時間，所以照時間排序）。"""
from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def txn_id() -> str:
    return "t_" + ulid()


def batch_id() -> str:
    return "b_" + ulid()
