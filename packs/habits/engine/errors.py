"""引擎「說不行」用的例外：bridge.as_app_error 只把這幾種變成給人看的一句話。

都是內建例外的子類別（舊的 except ValueError／KeyError 照樣接得到）；程式自己的錯（真的 KeyError、IndexError、
ValueError）不會被誤當成「找不到這一筆」，交給 SYNOX 記下來。"""


class Refused(ValueError):
    """輸入不合規則、或現在不准這樣做（訊息是給人看的一句話）。"""


class NotFound(KeyError):
    """找不到這一筆。"""


class NoCell(IndexError):
    """旅程裡沒有這一格。"""


class NoMatch(LookupError):
    """名字對不到、或對到好幾條（訊息是給人看的一句話）。"""
