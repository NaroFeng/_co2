# Chapter 11 — Jack 語言編譯器（Jack Compiler：Jack → VM）

> 本章直接把 `.jack` 原始碼翻譯成 Hack VM 的 `.vm` 程式碼，
> **跳過 Chapter 10 的 XML 中間表示**，全程不產生任何 XML 相關程式碼。

---

## 專案定位

| 項目 | 內容 |
|---|---|
| 章節 | Nand2Tetris Chapter 11（Project 11） |
| 主題 | 將 Jack 語言（物件導向的迷你語言）編譯成 Hack VM 組語 |
| 課程 | 計算機結構 — 金門大學資訊工程系（115 學年上學期，陳鍾誠 教師） |
| 學生 | 馮至豪（111410545） |
| 本章產出 | 純 Python 3 標準庫實作的 Jack → VM 編譯器（單檔輸入、單檔輸出） |
| 驗收工具 | 官方 `nand2tetris/tools/JackCompiler.bat` 與 `VMEmulator.bat`（OpenJDK 25） |

### 語意要點（實作依據）

| 項目 | 規格 | 本實作 |
|---|---|---|
| 運算子優先序 | Jack **沒有**優先序，全部由左至右 | `1 + 2 * 3` = `(1 + 2) * 3`（與官方編譯器一致） |
| 陣列賦值 | 位址必須活到「值」算完之後 | `<index> <base> add` → 留在堆疊；值算完後才 `pop temp 0` / `pop pointer 1` / `push temp 0` / `pop that 0` |
| `do` 敘述 | 丟棄被呼叫函式的回傳值 | `call …` 之後一律補 `pop temp 0` |
| `return;` | void 函式也要留一個值 | `push constant 0` → `return` |
| `method` 的接收者 | `this` 佔據 `argument 0` | 解析參數清單**之前**先註冊 `("this", className, "arg")` |
| 方法呼叫的參數順序 | 接收者必須先推入堆疊 | `push pointer 0` / `push <物件>` 都在計算引數**之前** |
| 欄位（field） | `function` 沒有 `this`，看不到 instance 欄位 | 函式內存取欄位會直接報錯（與官方編譯器一致） |
| 標籤唯一性 | 巢狀 `if` / `while` 不得撞名 | `IF_TRUE<n>` / `IF_FALSE<n>` / `IF_END<n>` / `WHILE_EXP<n>` / `WHILE_END<n>`，每個副程式各自從 0 起算 |

---

## 當前目錄產出物

| 檔案 | 說明 |
|---|---|
| `jack_compiler.py` | 純 Python 標準庫（`sys` / `pathlib` / `dataclasses` / `typing`）實作的 Jack → VM 編譯器，單檔切成四個類別：`JackTokenizer`（去註解 + 詞法分析 + 兩 token 預讀）、`SymbolTable`（class / subroutine 兩層作用域）、`VMWriter`（VM 指令輸出，另有 `mark()` / `insert_at()` 供 `function` 標頭回填）、`CompilationEngine`（遞迴下降剖析器）。 |
| `test_jack_compiler.py` | 本章的測試腳本：編譯全部 6 個官方專案、靜態檢查三個高風險地雷（陣列賦值 / `do` / `method` 前置碼）、與官方編譯器逐行比對、以及在官方 VM 模擬器上實際執行並檢查執行結果。 |
| `*/`（六個官方專案） | `Seven`、`ConvertToBin`、`Square`、`Average`、`Pong`、`ComplexArrays`；每個 `.jack` 對應一份由本編譯器產生的 `.vm`。 |

### 執行指令

```bash
python 11/jack_compiler.py <path/to/file.jack>   # 輸出同目錄同名 .vm
python 11/jack_compiler.py <path/to/folder>      # 資料夾內每個 .jack 各產生一份 .vm
python 11/test_jack_compiler.py                  # 跑完整測試（自動找官方工具）
python 11/test_jack_compiler.py --tools <tools>  # 指定官方工具所在目錄
```

---

## 驗證結果

### 1. 六個官方專案全部編譯成功

| 專案 | 來源檔 | 產出 |
|---|---|---|
| `Seven` | `Main.jack` | `Main.vm` |
| `ConvertToBin` | `Main.jack` | `Main.vm` |
| `Square` | `Main.jack` / `Square.jack` / `SquareGame.jack` | `Main.vm` / `Square.vm` / `SquareGame.vm` |
| `Average` | `Main.jack` | `Main.vm` |
| `Pong` | `Main.jack` / `Bat.jack` / `Ball.jack` / `PongGame.jack` | `Main.vm` / `Bat.vm` / `Ball.vm` / `PongGame.vm` |
| `ComplexArrays` | `Main.jack` | `Main.vm` |

共 11 份 `.vm`，全部無語法錯誤。

### 2. 與官方 `JackCompiler` 逐行比對

以官方 `JackCompiler.bat` 編譯同一批原始碼，再與本實作的輸出**逐行**比對：

> **11 / 11 份 `.vm` 與官方編譯器的輸出完全相同（byte-for-byte identical）。**

### 3. 靜態檢查（`test_jack_compiler.py`）

針對 `Seven` / `ConvertToBin` / `ComplexArrays`（以及其餘三個專案）檢查：

* 每個 `let a[i] = …` 的尾巴必須剛好是 `pop temp 0` / `pop pointer 1` / `push temp 0` / `pop that 0`，
  且位址的計算必須以 `add` 收尾（也就是**沒有**先把位址塞進 `THAT`）；
* 每個 `do` 敘述產生的 `call` 後面必須緊接著 `pop temp 0`；
* 每個 `method` 的前置碼必須是 `push argument 0` / `pop pointer 0`。

### 4. 在官方 `VMEmulator` 上實際執行

| 專案 | 檢查項目 | 結果 |
|---|---|---|
| `Seven` | `Output.printInt(1 + (2 * 3))` 印出的值 | `7` ✅ |
| `ConvertToBin` | `RAM[8000] = 12345` 轉換後 `RAM[8001..8016]` | `1 0 0 1 1 1 0 0 0 0 0 0 …`（12345 的 16 個位元）✅ |
| `ComplexArrays` | 依序印出的五個結果 | `5, 40, 0, 77, 110`（與程式自帶的 expected 完全相同）✅ |

測試腳本會用一個極小的 `Sys` / `Output` 類別取代官方 OS：**VM 模擬器的畫面記憶體並不在它的 RAM 裡**，
所以把「印出來的東西」改記錄到 RAM，才能在命令列模式下自動檢查結果。

### 5. 測試腳本總結果

```
OK: 444 checks passed
```

---

## 已知限制 / 注意事項

* 官方 `JackCompiler` **不支援 UTF-8 BOM**；本實作會自動忽略 BOM，兩者對同一份原始碼的判定可能因此不同。
* 官方編譯器在「method 內部以未限定名稱呼叫同類別的 function」時會報錯
  （`Function X.f called as a method`）；本實作改以 `call Class.f n` 呼叫，語意相同且較寬容。
* Jack 語言沒有字元常數（`char` 只是型別），因此 `let c = '/';` 兩者都會報錯。
