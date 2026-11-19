"""「現在」只從這裡拿（測試換掉 now 就好）。"""
from datetime import date, datetime


def now() -> datetime:
    return datetime.now()


def today() -> date:
    return now().date()
