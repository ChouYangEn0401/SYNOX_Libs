"""中文日期解析（packs/_shared/zhdate.py 的正本；各 pack 的副本由 test_shared_sync 保證一樣）。"""

from datetime import date

import pytest

from zhdate import cn_int, check_ymd, day_label, parse_when

MON = date(2026, 11, 9)          # 星期一


@pytest.mark.parametrize("text, day, start, end, rest", [
    ("明天 14:00 看牙醫", date(2026, 11, 10), "14:00", None, "看牙醫"),
    ("今天 開會", MON, None, None, "開會"),
    ("後天 交報告", date(2026, 11, 11), None, None, "交報告"),
    ("大後天 早上9點 去銀行", date(2026, 11, 12), "09:00", None, "去銀行"),
    ("下週一下午三點半 開會", date(2026, 11, 16), "15:30", None, "開會"),
    ("下下週三 回診", date(2026, 11, 25), None, None, "回診"),
    ("這週五 聚餐", date(2026, 11, 13), None, None, "聚餐"),
    ("週一 晨會", MON, None, None, "晨會"),                      # 今天就是週一：算今天
    ("星期日 爬山", date(2026, 11, 15), None, None, "爬山"),
    ("禮拜五 晚上8點15分 聚餐", date(2026, 11, 13), "20:15", None, "聚餐"),
    ("11/12 繳費", date(2026, 11, 12), None, None, "繳費"),
    ("11/3 生日", date(2027, 11, 3), None, None, "生日"),        # 今年的已經過了 → 明年
    ("11月20日 員工旅遊", date(2026, 11, 20), None, None, "員工旅遊"),
    ("十二月二十五號 聖誕節", date(2026, 12, 25), None, None, "聖誕節"),
    ("2026-11-20 14:00-15:30 面試", date(2026, 11, 20), "14:00", "15:30", "面試"),
    ("2027/1/5 10:00 看診", date(2027, 1, 5), "10:00", None, "看診"),
    ("3天後 交報告", date(2026, 11, 12), None, None, "交報告"),
    ("兩週後 回診", date(2026, 11, 23), None, None, "回診"),
    ("15號 繳卡費", date(2026, 11, 15), None, None, "繳卡費"),
    ("5號 繳房租", date(2026, 12, 5), None, None, "繳房租"),       # 這個月的 5 號過了 → 下個月
    ("今晚 7點 看電影", MON, "19:00", None, "看電影"),
    ("明早 6:30 跑步", date(2026, 11, 10), "06:30", None, "跑步"),
    ("下午2點到4點 上課", None, "14:00", "16:00", "上課"),
    ("9am 晨會", None, "09:00", None, "晨會"),
    ("2:30pm 打電話給客戶", None, "14:30", None, "打電話給客戶"),
    ("中午 吃飯", None, "12:00", None, "吃飯"),
    ("中午1點 吃飯", None, "13:00", None, "吃飯"),
    ("買一點點菜", None, None, None, "買一點點菜"),                 # 「一點點」不是一點鐘
    ("看牙醫 明天 下午3點", date(2026, 11, 10), "15:00", None, "看牙醫"),
    ("繳電費", None, None, None, "繳電費"),
    ("今天的事 今天", MON, None, None, "今天的事"),                # 前後有空白的那個才是日期
])
def test_parse(text, day, start, end, rest):
    w = parse_when(text, MON)
    assert not w.error
    assert (w.day, w.start, w.end, w.rest) == (day, start, end, rest)


@pytest.mark.parametrize("text", ["2/30 吃飯", "2026-13-01 x", "25:00 開會", "13pm x"])
def test_bad_dates_are_errors_not_guesses(text):
    w = parse_when(text, MON)
    assert w.error and w.day is None


def test_all_day_and_cn_numbers():
    assert parse_when("明天 員工旅遊", MON).all_day
    assert not parse_when("明天 9:00 x", MON).all_day
    assert [cn_int(s) for s in ("3", "三", "十", "十二", "二十三", "兩")] == [3, 3, 10, 12, 23, 2]
    with pytest.raises(ValueError):
        cn_int("百")


def test_check_and_labels():
    assert check_ymd("2026-11-12") is None
    assert check_ymd("2026-02-30") and check_ymd("11/12")
    assert day_label(MON, MON) == "今天 11/09（一）"
    assert day_label(date(2026, 11, 10), MON) == "明天 11/10（二）"
    assert day_label(date(2026, 11, 14), MON) == "11/14（六）"
    assert day_label(date(2027, 1, 2), MON) == "2027/01/02（六）"
