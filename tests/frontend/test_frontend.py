"""組員四：前端頁面與核心邏輯（狀態區分、ACK、去重、追蹤顯示格式）。

核心邏輯 frontend/js/core.js 以 Node 內建測試執行（tests/frontend/core.test.js）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_pages_served(h):
    for path, marker in [("/", "選擇角色"), ("/client.html", "依 Message ID 追蹤"), ("/ops.html", "規則式診斷"),
                         ("/js/core.js", "mergeStatus"), ("/js/client.js", "buildAck"), ("/js/ops.js", "/api/faults"),
                         ("/css/style.css", "st-DELIVERED")]:
        r = h.client.get(path)
        assert r.status_code == 200, path
        assert marker in r.text, path


def test_client_page_wires_ack_and_dedup():
    js = (ROOT / "frontend/js/client.js").read_text(encoding="utf-8")
    assert "C.buildAck(m.message_id)" in js
    assert "dedup.accept(m.message_id)" in js
    assert "C.mergeStatus" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 才能執行前端核心測試")
def test_core_js_with_node():
    r = subprocess.run(["node", "--test", str(ROOT / "tests/frontend/core.test.js")], capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
