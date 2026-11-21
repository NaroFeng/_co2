# Chapter 07 — VM I：堆疊算術與記憶體段（VM Translator）

👉 **線上互動視覺化教學網頁（免安裝、直接開啟）**：[Hack VM 互動模擬器與架構導覽](https://narofeng.github.io/_co2/08/visualizer.html)

> 網頁內建本章的 **`BasicTest`** 互動步進案例：可逐行觀察 `push / pop` 對 8 種記憶體段
> （`constant` / `local` / `argument` / `this` / `that` / `temp` / `pointer` / `static`）的存取，
> 以及 `LCL / ARG / THIS / THAT` 四個基底指標如何把「基底 + index」換算成實際位址；
> 右欄同步顯示由本章 `vm_translator.py` **真實翻譯**出的 Hack 組語。

---

## 專案定位

| 項目 | 內容 |
|---|---|
| 章節 | Nand2Tetris Chapter 07（Project 07） |
| 主題 | VM I：堆疊算術（`add/sub/neg/eq/gt/lt/and/or/not`）與 8 種記憶體段存取 |
| 課程 | 計算機結構 — 金門大學資訊工程系（115 學年上學期，陳鍾誠 教師） |
| 學生 | 馮至豪（111410545） |
| 本章產出 | 純 Python 標準庫實作的 VM → Hack 組語翻譯器（單檔輸入、單檔輸出） |
| 驗收工具 | 官方 `nand2tetris/tools/CPUEmulator.bat`（OpenJDK 25） |

### 語意要點（實作依據）

| 項目 | 規格 | 本實作 |
|---|---|---|
| 比較結果 | 真值 `-1`（`0xFFFF`）、假值 `0` | `eq/gt/lt` 皆以 `D = x - y` + 帶號跳轉實作 |
| 堆疊順序 | 先 push `x`、後 push `y` | `sub/gt/lt` 一律為 `x op y` |
| 標籤唯一性 | 每次比較需唯一遞增編號 | `IF_<JMP>_<n>` / `END_<JMP>_<n>`，由單調遞增計數器產生 |
| `static i` | `@<檔名不含副檔名>.<i>` | 例：`StaticTest.vm` 的 `static 3` → `@StaticTest.3` |
| `pointer i` | `pointer 0` = `THIS`(RAM[3])、`pointer 1` = `THAT`(RAM[4]) | 基底 `3 + index`，index 僅允許 `0` / `1`，其餘報錯 |

---

## 當前目錄產出物

| 檔案 | 說明 |
|---|---|
| `vm_translator.py` | 純 Python 標準庫（`sys` / `pathlib` / `dataclasses`）實作的**單趟** VM-to-ASM 翻譯器。切成三個職責區塊：`Parser`（讀 `.vm`、去註解與空白、解析為 `VMCommand(cmd_type, arg1, arg2)`）、`CodeWriter`（單一 VM 指令 → Hack 組語，每個方法回傳 `list[str]`）、`main`（CLI 與檔案輸出）。每段組語前自動加上 `// <原始 VM 指令>` 註解，並預留 `self.translations`（VM 指令 ↔ 組語行的對應資料）供視覺化對照使用。 |

### 執行指令

```bash
python 07/vm_translator.py <path/to/file.vm>     # 輸出同目錄同名 .asm（不寫入 Bootstrap）
```

### 產生的組語範例

```asm
// push constant 7
@7
D=A
@SP
A=M
M=D
@SP
M=M+1
// add
@SP
AM=M-1      ; A = SP-1 (y)，SP--
D=M         ; D = y
A=A-1       ; A = SP-2 (x)
M=D+M       ; *x = y + x
```

---

## Gemini × OpenCode 協作開發與 Code Review 紀錄

### 三方分工

| 角色 | 負責範圍 |
|---|---|
| **本人** | 需求規格定義、章節語意查核、每階段人工 Code Review 與放行決策 |
| **Gemini** | 底層架構設計（Parser / CodeWriter / main 切分）、組語模板 Code Review、觀念釐清與陷阱提示 |
| **OpenCode** | 本機實作、產生 `.asm`、呼叫官方 `CPUEmulator` 自動驗收與結果彙整 |

### 審查階段實際抓出並修正的 2 個技術問題

| # | 問題 | 徵狀 | 修正 |
|---|---|---|---|
| 1 | **`pointer` segment 映射錯誤** | 原將 `pointer i` 對應成 `@i`，導致 `pointer 0` 寫入 `RAM[0]` — 在 CPU Emulator 中 `RAM[0]` 就是 `SP`，堆疊指標被覆寫，`PointerTest` 必然失敗 | 依規格改為基底 `3 + index`：`pointer 0` = `THIS`(RAM[3])、`pointer 1` = `THAT`(RAM[4])；Parser 另加索引邊界檢查，`pointer 2` 以上直接報錯（含檔名:行號） |
| 2 | **間接定址為 O(i) 寫法** | `local/argument/this/that` 以「重複印出 i 次 `A=A+1`」計算位址，index 變大時產生碼呈 O(i) 膨脹 | 重構為 O(1) 標準寫法：先算 `base + i` 存入 `R13`（單一 VM 指令內的暫存空間），再從 `R13` 取值完成 push / pop；`R13` 與 Chapter 08 的 `R14`（返回位址）不衝突 |

---

## 測試驗收結果

全部以官方 `CPUEmulator.bat` 執行各章節 `.tst` 腳本，輸出與官方 `.cmp` 逐格比對。

| # | 測試 | 驗收內容 | CPUEmulator 輸出 | 結果 |
|---|---|---|---|---|
| 1 | `StackArithmetic/SimpleAdd` | 推入兩常數後 `add` | `End of script - Comparison ended successfully` | ✅ |
| 2 | `StackArithmetic/StackTest` | 9 組 `eq/gt/lt` 比較 + `and/or/not` 連鎖運算 | `End of script - Comparison ended successfully` | ✅ |
| 3 | `MemoryAccess/BasicTest` | `local / argument / this / that / temp` 五種 segment 存取 | `End of script - Comparison ended successfully` | ✅ |
| 4 | `MemoryAccess/PointerTest` | `pointer 0/1`（`THIS` / `THAT`）間接存取 | `End of script - Comparison ended successfully` | ✅ |
| 5 | `MemoryAccess/StaticTest` | `static` 段命名 `@StaticTest.<i>` | `End of script - Comparison ended successfully` | ✅ |
| | **合計** | | | **5 / 5 全綠** |

> Chapter 08 的 6 支測試請見 [`08/README.md`](../08/README.md)（連同本章合計 **11/11**）。
