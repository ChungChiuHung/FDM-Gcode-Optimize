# H2C G-Code Optimizer AI Agent Instructions

## 1. 核心開發哲學 (Reliable Engineering)
- **嚴格遵守單一職責原則 (SRP)**：單一檔案絕對不可超過 200 行。
- **Spec-First**：修改程式碼前，必須先閱讀 `specs/` 目錄下的規格定義。
- **Test-Driven**：沒有對應的 `tests/` 測試案例，就不允許修改 `src/` 核心邏輯。

## 2. FDM 3D 列印物理鐵律 (不可違背)
- **壓力補償隔離**：絕對不可刪除或翻轉純粹的 `E` 移動（Retractions / Wipes），必須將其「書擋 (Bookend)」在擠出路徑兩側。
- **圓弧完整性**：遇到 `G2/G3` 指令時，必須完整保留 `I`、`J`、`R`、`P` 參數，不可破壞齒輪的幾何半徑。
- **絕對坐標狀態**：必須隨時追蹤 `G90/G91` 與 `M82/M83` 狀態，避免坐標偏移災難。