import hashlib
from pathlib import Path


def test_report_prompts_remain_frozen():
    root = Path(__file__).resolve().parents[2]
    prompt_path = root / "src/fund_research_agent/legacy_port/report_engine/prompts.py"
    assert hashlib.sha256(prompt_path.read_bytes()).hexdigest() == (
        "0f303b39d42ab207fc5a2759eff4a24dffb65d77f347c476c00ef9eb83267e59"
    )
