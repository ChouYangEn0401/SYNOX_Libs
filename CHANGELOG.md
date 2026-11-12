# Changelog

工具庫本身的版本在 `VERSION`；每個工具的版本在自己的 `pack.json`（下面每行標出來）。格式照 Keep a Changelog。

## [Unreleased]

### Added
- 工具庫成立（2026-11-12，ADR-0001）：從 SYNOX 搬來 `money`、`notes`、`remind`，從 SYNOXPacks 搬來 `calendar`、`todo`、`bullet`
  （來源對照 [docs/sources.md](docs/sources.md)）。
- 六個工具都宣告 `requires.synox: ">=0.2"`、`requires.sdk: ">=0.2,<0.3"`，版本 0.1.0 → **0.1.1**。
- `scripts/build.py`：打包成 `dist/<name>-<version>.synoxpack`＋`dist/index.json`（雜湊、SDK 範圍、建議群組）；`catalog.json` 建議群組。
- 測試：六個工具的第 1、2 關、SDK 範圍、打包、空的 SYNOX 一個一個裝。

### Fixed
- `calendar` 0.1.1：`/cal_ics` 的暫存檔改寫在使用者自己的資料夾（原本寫在全部人共用的 `data/packs/calendar/`）。
