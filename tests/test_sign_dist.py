# -*- coding: utf-8 -*-
"""scripts/sign_dist.py：簽整批發佈檔，再用簽過的檔重算 index.json、SHA256SUMS、tools zip。

全部在暫存資料夾：做一份迷你 dist/（幾個假 pack＋別名、index.json、tools zip、SHA256SUMS，寫法跟 build.py 同一份），
用假的 synox-sdk（測試裡寫的小腳本：sign 在 zip 裡加 pack.sig.json、verify 看有沒有）跑完整流程；
最後一條用真的 SDK 與測試裡臨時產生的假鑰匙（標章是 author）。不連網、不開埠、不碰任何真的鑰匙。"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import pytest

import build as build_script
import links
import sign_dist

PASSWORD = "test-only-passphrase-123"
NAMES = ("alpha", "beta", "gamma")          # gamma 當外觀（kind=theme，不進 tools zip）

FAKE_SDK = r'''
import json, os, sys, zipfile
cmd, path = sys.argv[1], sys.argv[2]
log = os.environ.get("FAKE_SDK_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(cmd + " " + os.path.basename(path) + "\n")
def signed(p):
    with zipfile.ZipFile(p) as z:
        return any(n.endswith("pack.sig.json") for n in z.namelist())
if cmd == "verify":
    if signed(path):
        status = os.environ.get("FAKE_STATUS", "official")
        print("✅ 官方（%s）" % status if status == "official" else "✍️ 作者簽章（%s）" % status)
        sys.exit(0)
    print("⚠️ 不是官方（none）")
    sys.exit(1)
if cmd == "sign":
    if not os.environ.get("SYNOX_PACK_KEY_PASSWORD"):
        print("❌ 沒有密語"); sys.exit(2)
    fail = os.environ.get("FAKE_FAIL", "")
    if fail and fail in os.path.basename(path):
        print("❌ 壞了，密語是 " + os.environ["SYNOX_PACK_KEY_PASSWORD"]); sys.exit(2)
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i)) for i in z.infolist()]
    prefix = items[0][0].filename.split("/")[0] + "/"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in items:
            z.writestr(info, data)
        z.writestr(prefix + "pack.sig.json", json.dumps({"key": os.path.basename(sys.argv[4])}))
    print("✅ 簽好了：" + path)
    sys.exit(0)
sys.exit(2)
'''


def _pack_zip(path, name):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/pack.json", json.dumps({"name": name, "version": "1.0.0", "kind": "cli"}))
        z.writestr(f"{name}/main.py", f"print('{name}')\n")


def make_dist(dist):
    """迷你 dist/：跟 build.py 全部打包的輸出同一個樣子。"""
    os.makedirs(dist)
    entries = []
    for n in NAMES:
        file = f"{n}-1.0.0.synoxpack"
        _pack_zip(os.path.join(dist, file), n)
        with open(os.path.join(dist, file), "rb") as f:
            data = f.read()
        entries.append({"name": n, "title": n.title(), "version": "1.0.0", "kind": "theme" if n == "gamma" else "cli",
                        "line": "一句話", "file": file, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    index = {"library": "SYNOX_Libs", "library_version": "9.9.9", "built_with": {"synox": "0", "sdk": "0"},
             "packs": entries, "bundles": {}, "collections": {"wardrobe": {"packs": ["gamma"]}}}
    with open(os.path.join(dist, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_script.index_text(index))
    for e in entries:
        with open(os.path.join(dist, e["file"]), "rb") as src, open(os.path.join(dist, links.alias_file(e["name"])), "wb") as dst:
            dst.write(src.read())
    build_script.write_tools_zips(index, dist, "tools-9.9.9.zip")
    build_script.write_sums(dist, sign_dist.release_files(index))
    return index


def _snapshot(folder, skip=()):
    out = {}
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if os.path.join(base, d) not in skip]
        for n in files:
            p = os.path.join(base, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    work = tmp_path / "work"
    dist = str(work / "dist")
    index = make_dist(dist)
    keys = work / "keys"
    keys.mkdir()
    key = keys / "fake.pem"
    key.write_text("not a real key\n", encoding="utf-8")
    (work / "other.txt").write_text("leave me alone\n", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    sdk = tools / "fake_sdk.py"
    sdk.write_text(FAKE_SDK, encoding="utf-8")
    log = tools / "sdk.log"
    monkeypatch.setenv("FAKE_SDK_LOG", str(log))
    monkeypatch.setenv(sign_dist.PASSWORD_ENV, PASSWORD)
    for k in ("FAKE_FAIL", "FAKE_STATUS"):
        monkeypatch.delenv(k, raising=False)

    class Env:
        pass
    e = Env()
    e.work, e.dist, e.index, e.key, e.sdk, e.log = str(work), dist, index, str(key), str(sdk), log
    e.outside = lambda: _snapshot(e.work, skip=(dist,))
    e.calls = lambda: log.read_text(encoding="utf-8").split("\n")[:-1] if log.exists() else []
    e.run = lambda *extra: sign_dist.main(["--dist", dist, "--key", e.key, "--sdk", e.e_sdk, *extra])
    e.e_sdk = str(sdk)
    return e


def _read_index(dist):
    with open(os.path.join(dist, "index.json"), encoding="utf-8") as f:
        return json.load(f)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_signs_every_pack_and_recomputes_everything(env, capsys):
    outside = env.outside()
    assert env.run() == 0
    out = capsys.readouterr().out
    assert PASSWORD not in out
    assert "簽了 3 個" in out and "驗了 6 個" in out and "index.json" in out and "最後" in out
    new = _read_index(env.dist)
    for old_e, new_e in zip(env.index["packs"], new["packs"]):
        path = os.path.join(env.dist, new_e["file"])
        assert sign_dist.has_signature(path)
        assert new_e["sha256"] == _sha(path) != old_e["sha256"]
        assert new_e["size"] == os.path.getsize(path)
        # 其餘欄位一個字都不改
        assert {k: v for k, v in new_e.items() if k not in ("sha256", "size")} == \
               {k: v for k, v in old_e.items() if k not in ("sha256", "size")}
        assert _sha(os.path.join(env.dist, links.alias_file(new_e["name"]))) == _sha(path)   # 別名＝簽過的那一份
    assert {k: v for k, v in new.items() if k != "packs"} == {k: v for k, v in env.index.items() if k != "packs"}
    with open(os.path.join(env.dist, "index.json"), encoding="utf-8", newline="") as f:
        assert f.read() == build_script.index_text(new)
    # sha256sum -c 等價
    sums = sign_dist.read_sums(env.dist)
    assert set(sums) == set(sign_dist.release_files(new))
    assert all(_sha(os.path.join(env.dist, n)) == h for n, h in sums.items())
    # tools zip：簽過的檔、tools/index.json 同步、不含外觀、兩份一樣
    assert _sha(os.path.join(env.dist, "tools.zip")) == _sha(os.path.join(env.dist, "tools-9.9.9.zip"))
    with zipfile.ZipFile(os.path.join(env.dist, "tools-9.9.9.zip")) as z:
        assert sorted(z.namelist()) == ["tools/alpha-1.0.0.synoxpack", "tools/beta-1.0.0.synoxpack", "tools/index.json"]
        assert hashlib.sha256(z.read("tools/alpha-1.0.0.synoxpack")).hexdigest() == new["packs"][0]["sha256"]
        tools_index = json.loads(z.read("tools/index.json"))
        assert [e["sha256"] for e in tools_index["packs"]] == [e["sha256"] for e in new["packs"][:2]]
        assert "collections" not in tools_index
    assert sign_dist.problems(env.dist) == []
    assert env.outside() == outside                                    # dist 以外的檔一個都沒動
    assert not [n for n in os.listdir(env.dist) if n.startswith(sign_dist.STAGING_PREFIX)]
    assert [c for c in env.calls() if c.startswith("sign")] == [f"sign {n}-1.0.0.synoxpack" for n in NAMES]


def test_already_signed_packs_are_not_signed_again(env, capsys):
    assert env.run() == 0
    first = _snapshot(env.dist)
    env.log.unlink()
    assert env.run() == 0
    assert "簽了 0 個、本來就簽好的 3 個" in capsys.readouterr().out
    assert not [c for c in env.calls() if c.startswith("sign")]
    assert _snapshot(env.dist) == first                                # 重跑結果逐位元組一樣


def test_one_failure_stops_everything_and_leaves_dist_untouched(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_FAIL", "beta")
    before = _snapshot(env.dist)
    assert env.run() == 2
    out = capsys.readouterr().out
    assert "beta-1.0.0.synoxpack" in out and "停下" in out
    assert PASSWORD not in out and "***" in out                        # SDK 印出的密語被遮掉
    assert _snapshot(env.dist) == before                               # 不會半簽半不簽
    assert "sign gamma-1.0.0.synoxpack" not in env.calls()             # 壞了就不往下簽
    assert not [n for n in os.listdir(env.dist) if n.startswith(sign_dist.STAGING_PREFIX)]


def test_missing_password_is_an_error_and_nothing_runs(env, monkeypatch, capsys):
    monkeypatch.delenv(sign_dist.PASSWORD_ENV)
    before = _snapshot(env.dist)
    assert env.run() == 2
    out = capsys.readouterr().out
    assert sign_dist.PASSWORD_ENV in out and PASSWORD not in out
    assert env.calls() == []                                           # 連 SDK 都沒叫
    assert _snapshot(env.dist) == before


def test_wrong_badge_stops(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_STATUS", "author")                        # 例如拿了不是官方的鑰匙
    before = _snapshot(env.dist)
    assert env.run() == 2
    assert "預期 official" in capsys.readouterr().out
    assert _snapshot(env.dist) == before
    assert env.run("--expect", "author") == 0                          # 自己的鑰匙試跑：要求 author 就過


def test_incomplete_dist_is_refused(env, capsys):
    os.remove(os.path.join(env.dist, "beta.synoxpack"))
    before = _snapshot(env.dist)
    assert env.run() == 2
    assert "beta.synoxpack" in capsys.readouterr().out
    assert _snapshot(env.dist) == before and env.calls() == []


def test_hand_edited_index_is_refused(env, capsys):
    path = os.path.join(env.dist, "index.json")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace('"library": "SYNOX_Libs"', '"library":  "SYNOX_Libs"'))
    assert env.run() == 2
    assert "index.json" in capsys.readouterr().out


def test_check_mode(env, capsys):
    assert sign_dist.main(["--dist", env.dist, "--sdk", env.e_sdk, "--check"]) == 2     # 還沒簽
    assert env.run() == 0
    assert sign_dist.main(["--dist", env.dist, "--sdk", env.e_sdk, "--check"]) == 0
    with open(os.path.join(env.dist, "alpha.synoxpack"), "ab") as f:                   # 動了一個別名
        f.write(b"x")
    capsys.readouterr()
    assert sign_dist.main(["--dist", env.dist, "--sdk", env.e_sdk, "--check"]) == 2
    out = capsys.readouterr().out
    assert "alpha.synoxpack" in out and "SHA256SUMS" in out


def test_verify_output_parsing():
    assert sign_dist.STATUS_RE.search("✅ 官方（official）").group(1) == "official"
    assert sign_dist.STATUS_RE.search("⛔ 被改過（invalid）").group(1) == "invalid"
    assert sign_dist.STATUS_RE.search("✅ 官方") is None


def test_with_the_real_sdk_and_a_throwaway_key(tmp_path, monkeypatch, capsys):
    """真的 synox-sdk：keygen 一把用完即丟的鑰匙（暫存資料夾，不在任何 git 資料夾裡）→ 簽 → 驗；標章是 author。"""
    cmd = sign_dist.sdk_command("")
    probe = subprocess.run(cmd + ["--help"], stdin=subprocess.DEVNULL, capture_output=True, env=sign_dist._sdk_env())
    if probe.returncode != 0 or b"sign" not in probe.stdout:
        pytest.skip("這個環境叫不到 synox-sdk")
    monkeypatch.setenv(sign_dist.PASSWORD_ENV, PASSWORD)
    key = str(tmp_path / "keys" / "throwaway.pem")
    made = subprocess.run(cmd + ["keygen", "--out", key], stdin=subprocess.DEVNULL, capture_output=True,
                          env=sign_dist._sdk_env())
    assert made.returncode == 0, made.stdout.decode("utf-8", "replace")
    dist = str(tmp_path / "dist")
    make_dist(dist)
    assert sign_dist.main(["--dist", dist, "--key", key, "--expect", "author"]) == 0, capsys.readouterr().out
    assert PASSWORD not in capsys.readouterr().out
    assert sign_dist.problems(dist) == []
    assert sign_dist.main(["--dist", dist, "--check", "--expect", "author"]) == 0
    assert sign_dist.main(["--dist", dist, "--check"]) == 2            # 假鑰匙不會是 official
