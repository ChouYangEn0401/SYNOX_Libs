"""docs/mcp-audit-2026-11.md 裡每一段 kind=mcp 的 pack.json 範例，都要過 SYNOX 的第 1 關（嚴格）與第 2 關。"""

import json
import os
import re

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

from conftest import ROOT


def test_mcp_examples_in_audit_doc_are_valid(tmp_path):
    with open(os.path.join(ROOT, "docs", "mcp-audit-2026-11.md"), "r", encoding="utf-8") as f:
        blocks = re.findall(r"```json\n(.*?)```", f.read(), re.S)
    assert len(blocks) == 3
    for i, block in enumerate(blocks):
        raw = json.loads(block)
        folder = tmp_path / f"p{i}"
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
        (folder / "pack.json").write_text(json.dumps(raw), encoding="utf-8")
        m = synox_manifest.read(str(folder), strict=True)
        assert m.kind == "mcp" and m.mcp["tools_allow"]
        assert all(m.mcp["effects"][t] in ("READ", "WRITE", "DANGER") for t in m.mcp["tools_allow"])
        assert any(e == "DANGER" for e in m.mcp["effects"].values()), "刪除／改既有內容要有 DANGER"
        assert synox_scan.scan(m).problems(m) == []
