from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_operations_ticker_fields_are_required() -> None:
    template = Path("app/templates/ib_intelligence_operations.html").read_text(
        encoding="utf-8"
    )

    assert template.count('input name="tickers" required') == 3


def test_operations_payload_rejects_whitespace_only_tickers_before_fetch() -> None:
    script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("app/static/ib_market_intelligence.js", "utf8");
const context = { document: { addEventListener() {} } };
vm.runInNewContext(source, context);

const payload = context.buildIbmiPayload([
  ["module", "LIQUIDITY"],
  ["tickers", "   "],
]);
process.stdout.write(JSON.stringify({
  payload,
  validationMessage: context.ibmiValidationMessage(payload),
}));
"""

    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["payload"]["tickers"] == []
    assert result["validationMessage"] == "Enter at least one ticker."
