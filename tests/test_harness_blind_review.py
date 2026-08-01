import json

from scripts.build_harness_blind_review import main


def test_blind_packet_hides_arm_identity(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"rows": [
        {"caller": "pi_trading_decision", "case_id": "pi-001", "arm": "control", "content": "control"},
        {"caller": "pi_trading_decision", "case_id": "pi-001", "arm": "header", "content": "header"},
    ]}))
    output = tmp_path / "packet.json"
    monkeypatch.setattr("sys.argv", ["build", "--input", str(source), "--output", str(output), "--count", "1"])

    main()

    packet = json.loads(output.read_text())
    assert set(packet[0]) == {"case_id", "case_facts", "response_a", "response_b", "scores"}
    assert packet[0]["case_facts"]["rsi"] == 31
    assert {packet[0]["response_a"], packet[0]["response_b"]} == {"control", "header"}
