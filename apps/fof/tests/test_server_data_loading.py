from __future__ import annotations

from backtest_platform.server import csv_data_files


def test_csv_data_files_ignore_hidden_and_appledouble_files(tmp_path) -> None:
    tmp_path.joinpath("._000300.SH.csv").write_bytes(b"appledouble")
    tmp_path.joinpath(".hidden.csv").write_text("hidden", encoding="utf-8")
    tmp_path.joinpath("000300.SH.csv").write_text("trade_date,close\n20210104,1\n", encoding="utf-8")

    assert [path.name for path in csv_data_files(tmp_path)] == ["000300.SH.csv"]
