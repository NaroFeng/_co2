# `index.html` 製作規格書

這份文件是給「之後要幫忙把 03~13 章補齊、或是新增其他章節」的人（或下一個 session）看的，目的是讓每次新增的產出格式完全一致，不需再回頭猜測既有邏輯。

---

## 1. 檔案總覽

```
_co2/
├── index.html      ← 唯一交付物，所有資料內嵌於此（HTML + CSS + JS）
├── README.md       ← 含 GitHub Pages 連結
├── 01/             ← 資料夾 = 章節；HDL 與截圖放在這
│   ├── And.hdl
│   ├── And.png     ← 對應 And.hdl 的硬體模擬器截圖
│   └── ...
├── 02/
│   ├── FullAdder.hdl
│   ├── FullAdder.png
│   └── ...
└── ...
```

原則：
- `index.html` **不要**拆成多檔；為了在 GitHub Pages 直接部署，採單檔 SPA。
- 圖片與 HDL 留在原本的數字資料夾裡，不要複製到 `assets/` 集中。
- 圖片路徑一律以 `index.html` 所在根目錄為基準的相對路徑（例如 `01/And.png`）。

---

## 2. 頁面架構

採 **hash routing** 的單頁應用，沒有外部套件、不需要 build。

| URL | 畫面 |
| --- | --- |
| `#/`（或無 hash） | 首頁：所有章節的卡片網格 |
| `#/01` ~ `#/13` | 對應章節頁：每個晶片一張卡，含說明、截圖、HDL 程式碼 |

主畫面由兩個區塊組成：
1. **`<header class="site">`** — 黏在上方的標題列，含 logo（左）、麵包屑（右，`#crumbs`）。
2. **`<main id="app">`** — 由 JS 動態注入內容。
3. **`<footer class="site">`** — 學生姓名、學號，不變。

JS 入口點在 `<script>` 區塊尾端：
```js
window.addEventListener("hashchange", route);
route();
```

---

## 3. `CHAPTERS` 資料結構（最重要的部分）

所有內容都集中在 `<script>` 內的 `CHAPTERS` 物件中。新增章節 = 在這裡加一個 key。

### 3.1 章節物件 schema

```js
"NN": {                       // 字串 key，2 位數字、前補 0
  title:  "章節中文標題",       // 必填，顯示在卡片與章節頁 H2
  desc:   "一句話說明",         // 必填，顯示在卡片副標與章節頁 meta
  stub:   false,              // 選填，true 表示此章節尚未整理（卡片變灰、不可點）
  chips:  [                   // 該章節要展示的晶片 / 檔案 陣列
    {
      name:    "And",          // 必填，顯示為 <h3>
      summary: "說明這顆 gate 的設計重點（一句話即可）",
      img:     "01/And.png",   // 選填；單張用字串、多張用陣列；沒有就整個欄位省略
      code:    "CHIP And {...}", // 必填，HDL 原始碼；用 template literal 寫
    },
    // ...
  ],
}
```

### 3.2 範例（節錄現有 01）

```js
"01": {
  title: "布林邏輯 (Boolean Logic)",
  desc: "用 Nand 基礎閘組合出所有需要的邏輯閘。",
  chips: [
    {
      name: "And",
      summary: "Nand 串接兩次得到 And：a AND b = NOT(NAND(a,b))。",
      img: "01/And.png",
      code:
`CHIP And {
    IN a, b;
    OUT out;
    PARTS:
    Nand(a=a, b=b, out=aNandb);
    Nand(a=aNandb, b=aNandb, out=out);
}`,
    },
    // ...
  ],
},
```

### 3.3 Stub 章節

03~13 目前在 `STUBS` 陣列中被自動建為佔位章節：

```js
const STUBS = ["03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13"];
const STUB_TITLES = {
  "03": "時序邏輯 (Sequential Logic)",
  "04": "機器語言 (Machine Language)",
  // ...
};
STUBS.forEach(n => {
  CHAPTERS[n] = { title: STUB_TITLES[n] || `第 ${n} 章`, desc: "尚待整理。", chips: [], stub: true };
});
```

要把某一章從 stub 升級成完整內容：
1. 從 `STUBS` 陣列移除該編號。
2. 在 `CHAPTERS` 用完整 schema 重新定義（同 schema 覆蓋 stub）。
3. 保留 `title`、`desc`、`chips`；不需要再寫 `stub: false`。

---

## 4. 截圖掛載規則

### 4.1 一個 chip 對一張圖
直接把 `img` 設為字串：
```js
img: "01/And.png",
```

### 4.2 一個 chip 對多張圖
把 `img` 設為陣列，會垂直堆疊：
```js
img: ["02/HalfAdder.png", "02/FullAdder.png"],
```

> 註：早期 02 裡有一張 `螢幕截圖 2021-10-07 上午9.51.16.png`（Nand2Tetris 教材原始截圖，2021 年），與你後續自己截的 `02/ALU.png`、`02/Add16.png` 不是同一份畫面；製作時一律以「自己新增的、同檔名命名的 PNG」為主，不要把舊的中文檔名拿來當 ALU / Add16 的圖。

### 4.3 沒有圖
直接省略 `img` 欄位（不要寫 `img: null` 或空陣列）。

### 4.4 命名慣例
- 截圖檔名應與 HDL 同名（`.hdl` → `.png`），方便一眼對應。
- 例外：`DMux4Way` 的截圖叫 `DMux4.png`、`ALU` 的截圖是中文檔名（見下節）。

---

## 5. 中文檔名 / 含空白檔名

資料夾 02 裡有這張圖：
```
02/螢幕截圖 2021-10-07 上午9.51.16.png
```

HTML 用 `encodeImgPath()` 動態編碼，所以可以照原樣寫在資料裡：
```js
img: ["02/螢幕截圖 2021-10-07 上午9.51.16.png"],
```

背後是：
```js
function encodeImgPath(p) {
  return p.split("/").map(encodeURIComponent).join("/");
}
```

→ 瀏覽器實際請求：`02/%E8%9E%A2%E5%B9%95%E6%88%AA%E5%9C%96%202021-10-07%20%E4%B8%8A%E5%8D%889.51.16.png`

**新增章節時，若截圖含中文或空白，照原樣寫字串即可，不要預先 URL encode。**

---

## 6. 程式碼內嵌慣例

- 用 JS template literal（backtick）包 HDL 程式碼，保持縮排。
- 過長的可省略中間段，但要在註解寫 `/* ... 略, And() x16 ... */`，避免讀者誤會。
- 不要把註解行（`// This file is part of www.nand2tetris.org` 那幾行）放進 `code`，那是 NAND2Tetris 教材的 boilerplate，會讓畫面很雜。
- `summary` 寫設計思路 / 重點，不要直接抄 HDL 開頭那段 `/** ... */`。

---

## 7. CSS 設計 token

若要調整主題色，改 `:root` 的 CSS 變數即可，其他選擇器都用變數引用：

| 變數 | 用途 |
| --- | --- |
| `--bg` / `--bg-2` / `--bg-3` | 三層深色背景（頁面 / 卡片 / 卡片 hover） |
| `--fg` | 主要文字色 |
| `--muted` | 次要文字（描述、麵包屑） |
| `--accent` / `--accent-2` | 紫 / 青綠 漸層主色，用於 logo、章節編號、晶片標題 |
| `--border` | 卡片與分隔線 |
| `--code-bg` | `<pre>` 區塊背景 |

字體：`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` 用於程式碼；正文使用系統字體（含 `Noto Sans TC`、`Microsoft JhengHei` 確保中文）。

---

## 8. README 規則

`README.md` 上方是固定表格（學期 / 教師 / 學校 / 學生 / 學號），**不要動**。下方有一段：

```md
## 作業展示網頁

👉 [https://narofeng.github.io/_co2/](https://narofeng.github.io/_co2/)
```

網址根據 repo 實際位置決定，不要擅自改成 `localhost`。

---

## 9. 發佈到 GitHub Pages（給未來的自己）

1. `git add index.html README.md && git commit -m "..."`
2. `git push`
3. 到 GitHub repo → **Settings → Pages → Build and deployment**
4. Source 選 **Deploy from a branch**
5. Branch 選 `main`（或 `master`）、資料夾選 `/ (root)` → Save
6. 等約 30 秒，網址 `https://<user>.github.io/<repo>/` 即可瀏覽

如果換了 repo 名稱，README 內的連結要一起改。

---

## 10. 新增一個完整章節的 SOP（cheatsheet）

假設要補完 `07`（VM I）：

1. 確認 `07/` 資料夾內有哪些檔案與截圖。
2. 從 `STUBS` 陣列移除 `"07"`。
3. 在 `CHAPTERS["07"]` 用完整 schema 填內容：
   - `title` / `desc`：一句中文說明。
   - `chips`：每個要展示的檔案一個物件，依「§3 資料結構」填寫。
   - 截圖路徑照檔名原樣寫，含中文 / 空白也照寫（§5）。
   - 程式碼用 template literal 嵌入，移除教材 boilerplate。
4. 檢查 `STUB_TITLES["07"]` 仍正確（會被覆蓋沒關係）。
5. 本機用 `python -m http.server` 起簡單伺服器，眼睛看一次。
6. 連同 README（若 repo 網址有變）一起 commit / push。
