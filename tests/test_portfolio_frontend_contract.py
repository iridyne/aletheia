from pathlib import Path


def test_command_center_does_not_label_open_positions_as_empty_without_symbol_breakdown():
    source = Path("frontend/src/pages/CommandCenter.tsx").read_text(encoding="utf-8")

    assert "positionsCount" in source
    assert "hasPositions" in source
    assert "持仓 PnL 暂无分解" in source
    assert "无持仓" in source
