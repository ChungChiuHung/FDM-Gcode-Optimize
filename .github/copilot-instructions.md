# Agentic Workflow Rules
- **Spec-First**: 任何代碼實作前，必須先在 `specs/` 下確認規格。
- **Modular Isolation**: 核心邏輯必須放在 `src/modules/`，單一檔案嚴禁超過 200 行。
- **TDD Mandatory**: 必須先寫測試程式碼，再寫實作程式碼。
- **Environment**: 永遠使用 `uv run` 執行指令，確保環境隔離。