"""寫檢討的提供者（engine/services/llm.py ＋ reviews.run_one／run_due）：引擎不認得任何 AI，只叫交進來的函式。

測試只用假的提供者（不叫任何 AI、不開任何程式）。
"""
import pytest

from money_engine import db
from money_engine.errors import LlmUnavailable
from money_engine.services import llm, reviews

from .reviewdata import seed
from .test_reviews import good_answer


@pytest.fixture
def conn():
    c = db.connect()
    seed(c)
    return c


class Fake:
    """假的提供者：記下每一次叫它拿到的東西，回事先給好的答案（或丟事先給好的錯）。"""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def __call__(self, prompt, schema, opts):
        self.calls.append({"prompt": prompt, "schema": schema, "opts": opts})
        if self.error is not None:
            raise self.error
        return {"structured": self.answer, "model": "fake-model", "cost_usd": 0.01}


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]


def test_no_provider_is_llm_unavailable_and_writes_nothing(conn):
    seq = db.write_seq(conn)
    for call in (lambda: reviews.run_due(conn, None), lambda: reviews.run_one(conn, "month", "2037-09", None)):
        with pytest.raises(LlmUnavailable) as no:
            call()
        assert no.value.code == "llm_unavailable" and "review_save" in no.value.hint
    assert count(conn) == 0 and db.write_seq(conn) == seq
    with pytest.raises(LlmUnavailable):
        llm.require("not a function")


def test_provider_writes_one_review_that_is_validated(conn):
    fake = Fake(good_answer())
    rv = reviews.run_one(conn, "month", "2037-09", fake)
    assert rv["status"] == "done" and rv["grade"] == "B" and rv["model"] == "fake-model" and rv["cost_usd"] == 0.01
    assert "❌" not in rv["title"] and rv["attempts"] == 1                 # 跟手動存的一樣照 validate 清乾淨
    call = fake.calls[0]
    assert "2037-09" in call["prompt"] and call["schema"]["type"] == "object" and call["opts"]["model"] is None


def test_quota_defers_and_failure_is_recorded(conn):
    rv = reviews.run_one(conn, "month", "2037-09", Fake(error=llm.ProviderQuota("額度用完", resets="明天")))
    assert rv["status"] == "deferred" and "重置：明天" in rv["error"] and rv["next_attempt_after"]
    rv = reviews.run_one(conn, "month", "2037-09", Fake(error=llm.ProviderFailed("連不上")))
    assert rv["status"] == "failed" and rv["error"] == "連不上" and rv["attempts"] == 2
    rv = reviews.run_one(conn, "month", "2037-09", Fake(error=RuntimeError("內部細節 secret-xyz")))
    assert rv["status"] == "failed" and "secret" not in rv["error"] and "RuntimeError" in rv["error"]
    rv = reviews.run_one(conn, "month", "2037-09", Fake(answer="不是物件"))
    assert rv["status"] == "failed" and "格式" in rv["error"]


def test_run_due_does_every_due_one_and_stops_at_quota(conn):
    out = reviews.run_due(conn, Fake(good_answer()))
    assert [r["key"] for r in out["done"]] == ["2037-09", "2037-W40"]
    assert [r["key"] for r in out["skipped"]] == ["2037-W38", "2037-W39", "2037-W41"]   # W41 沒有記帳：略過、不叫 AI
    c2 = db.connect(":memory:")
    seed(c2)
    quota = Fake(error=llm.ProviderQuota())
    out = reviews.run_due(c2, quota)
    assert len(out["deferred"]) == 1 and len(quota.calls) == 1                # 額度用完：剩下的等下次
