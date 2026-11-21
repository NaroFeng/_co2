# Chapter 08 — VM II：程式流程控制、函式呼叫與多檔 Bootstrap（VM Translator）

👉 **線上互動視覺化教學網頁（免安裝、直接開啟）**：[Hack VM 互動模擬器與架構導覽](https://narofeng.github.io/_co2/08/visualizer.html)

> 單一離線 HTML 檔，提供 3 大功能：
> 1. **架構導覽** — `.vm → Parser → CodeWriter → .asm → CPUEmulator` 流程圖，點擊節點展開真實程式碼（直接摘自 `vm_translator.py`）。
> 2. **三欄式 Stack Frame 即時步進** — 左欄 `.vm` 原始碼（依檔案分組、目前行高亮）／中欄暫存器 `SP/LCL/ARG/THIS/THAT` 與視覺化堆疊（依 frame 畫分層括號，色塊區分 `ARG 區`、`Saved Frame`、`LCL 區`、工作堆疊）／右欄同步顯示該行 `.vm` 對應的**真實** Hack 組語。內建 `BasicTest`、`SimpleFunction`、`FibonacciElement` 三支案例。
> 3. **零後端一鍵複製狀態問 AI 助教** — 把「當前指令 + 真實 `.asm` + 當下 `SP/LCL/ARG/THIS/THAT` + 堆疊內容」組裝成結構化繁體中文 Prompt，複製到任何 AI 工具。

---

## 專案定位

| 項目 | 內容 |
|---|---|
| 章節 | Nand2Tetris Chapter 08（Project 08） |
| 主題 | VM II：程式流程控制（`label/goto/if-goto`）、函式呼叫（`function/call/return`）與 Stack Frame 協定、多檔 Bootstrap |
| 課程 | 計算機結構 — 金門大學資訊工程系（115 學年上學期，陳鍾誠 教師） |
| 學生 | 馮至豪（111410545） |
| 本章產出 | 完整支援 Ch 07 + Ch 08 指令集的翻譯器 + 自動建置的互動教學網頁 |
| 驗收工具 | 官方 `nand2tetris/tools/CPUEmulator.bat`（OpenJDK 25） |

### Stack Frame 協定（本章核心）

`call f nArgs` 推入的堆疊佈局（由低位址往高位址）：

```
arg 0 .. arg n-1  →  return addr (frame-5)  →  saved LCL (frame-4)
                  →  saved ARG (frame-3)    →  saved THIS (frame-2)
                  →  saved THAT (frame-1)   →  local 0 .. local k-1
```

| 項目 | 規格 | 本實作 |
|---|---|---|
| 標籤作用域 | 函式 `F` 內為 `F$label`，函式外維持 `label` | 由 `CodeWriter.current_function` 追蹤，作用域前綴自動套用 |
| 返回位址 | `*(frame - 5)` | `return` 時**先**存入 `R14`，**之後**才執行 `*ARG = pop()` |
| `nArgs == 0` | ARG 恰好指向 `frame-5` 本身 | 先存 `R14` 即為防覆寫保險，回傳值不會蓋掉返回位址 |
| Bootstrap | 資料夾／多檔才寫入 | 單檔輸入不寫；資料夾模式寫入 `SP = 256` + 等同 `call Sys.init 0` 的完整 frame |

---

## 當前目錄產出物

| 檔案 | 說明 |
|---|---|
| `vm_translator.py` | 完整支援 Ch 07 + Ch 08 指令集的 VM-to-ASM 翻譯器（純 Python 標準庫）。含函式作用域標籤 `F$label`、`call` / `return` 的 Stack Frame 實作、資料夾多檔模式並自動注入 Bootstrap、跨檔 `static` 命名（`<檔名>.<index>`）。沿用 Ch 07 的 `Parser` / `CodeWriter` / `main` 三段式與 `self.translations` 結構。 |
| `build_visualizer.py` | 純 Python 建置腳本。直接 `import vm_translator` 翻譯官方測試檔並把**真實組語**內嵌進網頁；同時內建 headless 驗證：以 `node` 執行網頁內的 JS 模擬器，逐格比對官方 `.cmp` 共 16 個記憶體格，**任一格不符即中止建檔**。 |
| `visualizer.html` | 完全離線、零框架、零外部依賴的單一互動教學 HTML 檔（內嵌 Vanilla CSS + Vanilla JS + JSON 資料）。內建 `BasicTest`（8 種記憶體段）、`SimpleFunction`（單層 frame 與 `return` 還原）、`FibonacciElement`（Bootstrap、多檔 `Sys.vm`+`Main.vm`、遞迴 frame 疊加）三支案例。 |

### 執行指令

```bash
# 翻譯：單一 .vm 檔（輸出同目錄同名 .asm，不寫入 Bootstrap）
python 08/vm_translator.py 08/ProgramFlow/BasicLoop/BasicLoop.vm

# 翻譯：整個資料夾（輸出 <資料夾>/<資料夾名>.asm，自動注入 Bootstrap）
python 08/vm_translator.py 08/FunctionCalls/FibonacciElement

# 重新建置互動教學網頁（含 headless 驗證，通過才輸出 HTML）
python 08/build_visualizer.py
```

---

## Gemini × OpenCode 協作開發與關鍵技術決策

### 三方協作模式

| 角色 | 負責範圍 |
|---|---|
| **本人** | 關鍵規格定義、章節語意查核、每階段人工 Code Review 與放行決策 |
| **Gemini** | 第一性原理的架構設計、組語模板 Code Review、零框架解耦與視覺化資料流設計 |
| **OpenCode** | 本機實作、產生 `.asm`、呼叫官方 `CPUEmulator` 自動驗收、網頁建置與 headless 驗證 |

### 實作與審查中的關鍵技術點

| # | 技術點 | 說明 |
|---|---|---|
| 1 | **Stack Frame 與 `nArgs == 0` 防覆寫** | `return` 先將 `*(frame - 5)` 存進 `R14`，再寫入 `*ARG = pop()`；還原 `THAT / THIS / ARG / LCL` 時以 `R13` 作遞減游標（`@R13 / AM=M-1 / D=M`）依次取用 `frame-1 … frame-4`，並以 `D=M+1` 讓 `SP = ARG + 1` 省下一行。 |
| 2 | **真實資料綁定與自動化 QA** | 網頁顯示的 **734 行** Hack 組語全部由 `08/vm_translator.py` 實際翻譯產出（建置腳本逐行比對內嵌組語與真實 `.asm` 檔確認一致），JS 端沒有任何手寫假組語。建置腳本的自動比對機制也實際攔截並修正了 JS 模擬器 `pop()` 讀取「堆疊陣列過期值」的 Bug（`return` 會把回傳值寫回 `*ARG` 參數槽，陣列未同步導致 `fib(4)` 被算成 5）。 |
| 3 | **VM 角色釐清** | `vm_translator.py` 屬於**編譯期**的 Process VM 翻譯器（`.vm` → `.asm`）；`visualizer.html` 內的 JS 狀態機屬於**執行期**模擬器（直接執行 `.vm` 語意並逐步快照）。兩者職責分離，故網頁的 `.asm` 一定來自真正的翻譯器。附帶說明：`Sys.vm` 結尾的 `label END / goto END` 是裸機架構沒有 `HALT` 指令時的結束駐留設計（官方 CPU Emulator 亦以此方式收尾）。 |

---

## 測試驗收結果

以官方 `CPUEmulator.bat` 執行各章節 `.tst` 腳本，輸出與官方 `.cmp` 逐格比對。

| # | 測試 | 驗收內容 | CPUEmulator 輸出 | 結果 |
|---|---|---|---|---|
| 1 | `ProgramFlow/BasicLoop` | `label / goto / if-goto` 迴圈與累加 | `End of script - Comparison ended successfully` | ✅ |
| 2 | `ProgramFlow/FibonacciSeries` | 迴圈 + `pointer` / `that` 段寫入斐波那契數列 | `End of script - Comparison ended successfully` | ✅ |
| 3 | `FunctionCalls/SimpleFunction` | 單一 frame 的 `function` / `return` 與四指標還原 | `End of script - Comparison ended successfully` | ✅ |
| 4 | `FunctionCalls/FibonacciElement` | Bootstrap + 多檔（`Sys.vm` + `Main.vm`）+ 遞迴 frame 疊加 | `End of script - Comparison ended successfully` | ✅ |
| 5 | `FunctionCalls/NestedCall` | 三層巢狀呼叫與 `THIS` / `THAT` 的保存還原 | `End of script - Comparison ended successfully` | ✅ |
| 6 | `FunctionCalls/StaticsTest` | 跨檔 `static` 段命名（`Class1.*` / `Class2.*`） | `End of script - Comparison ended successfully` | ✅ |
| | **本章合計** | | | **6 / 6 全綠** |
| | **連同 Chapter 07** | | | **11 / 11 全綠** |

### 網頁內 JS 模擬器的獨立驗證

`build_visualizer.py` 產檔前會以 `node` headless 執行網頁內的 JS 模擬器，並逐格比對官方 `.cmp`：

| 案例 | 步數 | 比對格數 | 結果 |
|---|---|---|---|
| `BasicTest` | 25 | 8 | 全部相符 ✅ |
| `SimpleFunction` | 10 | 6 | 全部相符 ✅ |
| `FibonacciElement` | 123 | 2 | 全部相符 ✅ |

> Chapter 07 的章節說明與其餘 5 支測試請見 [`07/README.md`](../07/README.md)。
