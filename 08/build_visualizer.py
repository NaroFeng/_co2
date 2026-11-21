#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the single-file interactive teaching visualiser for the Chapter 08
VM Translator.

    python 08/build_visualizer.py

What it does
------------
1. Imports ``08/vm_translator.py`` (the real translator) and uses it to
   translate the three teaching cases *for real*:
       * 07/MemoryAccess/BasicTest/BasicTest.vm
       * 08/FunctionCalls/SimpleFunction/SimpleFunction.vm
       * 08/FunctionCalls/FibonacciElement/{Main.vm, Sys.vm}
   Every ``.asm`` block shown in the web page is therefore produced by the
   translator — nothing is hand written.
2. Reads the official ``.tst`` files (for the initial memory image) and the
   official ``.cmp`` files (for the expected results).
3. Extracts the real source code of the translator's core methods so that
   the "architecture" tab shows the genuine implementation.
4. Emits ONE self contained file ``08/visualizer.html`` containing inline
   CSS, inline JavaScript and the case data as
   ``<script id="vm-data" type="application/json">``.
   No framework, no CDN, no external font, no ``fetch()`` — double click
   the file and it works.
5. Verifies the JavaScript simulator head-less (with ``node``) against the
   official ``.cmp`` files, so the page cannot show wrong numbers.

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import vm_translator as vt  # noqa: E402  (the real translator)


# ==========================================================================
# SECTION 1 — reading the official test assets
# ==========================================================================


def parse_tst_memory(tst_path: Path) -> Dict[int, int]:
    """Return the ``set RAM[n] v`` image of a ``.tst`` script."""
    memory: Dict[int, int] = {}
    text = tst_path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        match = re.match(r"^set\s+RAM\[(\d+)\]\s+(-?\d+)\s*,?$", line)
        if match:
            memory[int(match.group(1))] = int(match.group(2))
    return memory


def parse_cmp(cmp_path: Path) -> List[Tuple[int, int]]:
    """Return the ``[(address, expected value), ...]`` of a ``.cmp`` file."""
    labels: List[int] = []
    expected: List[Tuple[int, int]] = []
    for raw in cmp_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if all(re.fullmatch(r"RAM\[\d+\]?", c) for c in cells):
            labels = [int(re.search(r"\d+", c).group()) for c in cells]
            continue
        try:
            values = [int(c) for c in cells]
        except ValueError:
            continue
        if len(labels) == len(values):
            expected.extend(zip(labels, values))
    return expected


def read_vm_source(vm_path: Path) -> List[str]:
    """Return the raw lines of a ``.vm`` file (for the source view)."""
    return vm_path.read_text(encoding="utf-8").splitlines()


# ==========================================================================
# SECTION 2 — translating a case with the real translator
# ==========================================================================


def translate_case(
    vm_paths: List[Path], bootstrap: bool
) -> Tuple[List[dict], List[dict], Dict[str, str]]:
    """Translate the given ``.vm`` files with the real translator.

    Returns ``(instructions, files, ret_labels)`` where ``instructions`` is
    the flat, execution ordered instruction list (each entry carries the
    real ``.asm`` block) and ``ret_labels`` maps the instruction index of a
    generated return label to its name.
    """
    files: List[dict] = []
    instructions: List[dict] = []
    ret_labels: Dict[str, str] = {}
    label_targets: Dict[str, int] = {}

    for vm_path in vm_paths:
        file_index = len(files)
        source_lines = read_vm_source(vm_path)
        files.append({"name": vm_path.name, "lines": source_lines})

        writer = vt.CodeWriter(module_name=vm_path.stem)
        commands = vt.Parser(vm_path).parse()

        for command in commands:
            # resolve the label scope with the translator's own logic
            scoped = None
            if command.cmd_type in ("label", "goto", "if-goto"):
                scoped = writer.scoped_label(command.arg1)

            asm = writer.write_command(command)

            entry = {
                "f": file_index,
                "ln": command.source and _line_of(source_lines, command.source) or 0,
                "cmd": command.cmd_type,
                "a1": command.arg1,
                "a2": command.arg2,
                "text": command.source,
                "asm": asm,
                "scope": writer.current_function,
            }

            if command.cmd_type == "label":
                label_targets[scoped] = len(instructions)
            elif command.cmd_type in ("goto", "if-goto"):
                entry["target"] = -1  # resolved after the whole program is known
                entry["targetName"] = scoped
            elif command.cmd_type == "call":
                # the return label is the last line of the generated block
                ret_name = asm[-1].strip().strip("()")
                entry["retName"] = ret_name

            instructions.append(entry)

    # second pass: resolve the jump targets now that every label is known
    for index, entry in enumerate(instructions):
        if entry["cmd"] in ("goto", "if-goto"):
            entry["target"] = label_targets.get(entry["targetName"], -1)
        if entry["cmd"] == "call":
            fn_name = entry["a1"]
            key = f"function:{fn_name}"
            if key not in label_targets:
                label_targets[key] = -1
            entry["target"] = _function_index(instructions, fn_name)
            ret_labels[str(index + 1)] = entry["retName"]

    return instructions, files, ret_labels


def _line_of(source_lines: List[str], text: str) -> int:
    """Return the 1-based line number of ``text`` inside ``source_lines``."""
    for number, line in enumerate(source_lines, start=1):
        if line.split("//", 1)[0].strip() == text:
            return number
    return 0


def _function_index(instructions: List[dict], function_name: str) -> int:
    """Return the instruction index of ``function <function_name>``."""
    for index, entry in enumerate(instructions):
        if entry["cmd"] == "function" and entry["a1"] == function_name:
            return index
    return -1


def function_argument_hint(instructions: List[dict]) -> Dict[int, int]:
    """For every ``function`` instruction: how many argument slots it uses."""
    hints: Dict[int, int] = {}
    current = -1
    count = 0
    for index, entry in enumerate(instructions):
        if entry["cmd"] == "function":
            if current >= 0:
                hints[current] = count
            current = index
            count = 0
        elif entry["cmd"] == "push" and entry["a1"] == "argument":
            count = max(count, int(entry["a2"]) + 1)
    if current >= 0:
        hints[current] = count
    return hints


# ==========================================================================
# SECTION 3 — real source snippets of the translator
# ==========================================================================


def extract_method(source: str, method_name: str) -> str:
    """Return the genuine source of a method of ``vm_translator.py``."""
    marker = f"def {method_name}("
    start = source.index(marker)
    rest = source[start:].split("\n")
    block = [rest[0]]
    for line in rest[1:]:
        if line.startswith("    def ") or line.startswith("class "):
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block)


def build_architecture(source: str) -> List[dict]:
    """Build the clickable nodes of the architecture tab."""
    return [
        {
            "id": "vm",
            "title": ".vm 原始碼",
            "icon": "📄",
            "desc": (
                "Hack VM 的高階組語：每行一條指令，分成兩大類 ——"
                "堆疊運算（add/sub/neg/eq/gt/lt/and/or/not）與"
                "記憶體存取（push/pop segment i）。"
                "Chapter 08 再加入流程控制（label/goto/if-goto）"
                "與函式呼叫（function/call/return）。"
            ),
            "code": extract_method(source, "parse"),
        },
        {
            "id": "parser",
            "title": "Parser",
            "icon": "🧩",
            "desc": (
                "讀入 .vm，逐行去掉 // 註解與空白，"
                "再把每一行解析成結構化指令 VMCommand(cmd_type, arg1, arg2, source)。"
                "arg1/arg2 會在這裡就完成格式與範圍驗證"
                "（例如 pointer 只能取 0 或 1），錯誤訊息會帶上「檔名:行號」。"
                "source 欄位保留原始指令文字，之後會成為 .asm 裡的 // 註解。"
            ),
            "code": extract_method(source, "parse")
            + "\n\n"
            + extract_method(source, "_parse_memory_args"),
        },
        {
            "id": "codewriter",
            "title": "CodeWriter",
            "icon": "⚙️",
            "desc": (
                "把「單一」VM 指令翻譯成 Hack 組語，每個方法都回傳 list[str]。"
                "write_command() 會在每段組語前面補上 // <原始 VM 指令> 註解，"
                "並把 (指令, 組語) 對應存進 self.translations —— "
                "這正是本視覺化頁面與未來圖解功能的資料來源。"
                "間接定址一律用 R13 作為單一指令內的暫存空間，"
                "因此產生碼的長度與 index 大小無關（O(1)）。"
            ),
            "code": extract_method(source, "_translate")
            + "\n\n"
            + extract_method(source, "_segment_address_in_r13"),
        },
        {
            "id": "frame",
            "title": "Stack Frame 協定",
            "icon": "🧱",
            "desc": (
                "call f nArgs 會依序推入：arg 0..n-1 → return address(frame-5) "
                "→ saved LCL(frame-4) → saved ARG(frame-3) → saved THIS(frame-2) "
                "→ saved THAT(frame-1)，接著由被呼叫端配置 local 0..k-1。"
                "return 必須先把 *(frame-5) 存進 R14，再執行 *ARG = pop()，"
                "否則 nArgs == 0 時 ARG 正好指向返回位址本身，"
                "回傳值會把返回位址蓋掉。"
            ),
            "code": extract_method(source, "write_call")
            + "\n\n"
            + extract_method(source, "write_return"),
        },
        {
            "id": "bootstrap",
            "title": "Bootstrap",
            "icon": "🚀",
            "desc": (
                "當輸入是「資料夾」或「多個 .vm 檔」時，譯�器會在產出的 .asm "
                "最前面寫入 bootstrap：SP = 256，等同於 call Sys.init 0 —— "
                "推入返回位址與 LCL/ARG/THIS/THAT、設定 ARG = SP-5-0、LCL = SP，"
                "最後跳進 Sys.init。"
                "單一 .vm 檔則不寫 bootstrap（由 .tst 自行準備記憶體狀態）。"
            ),
            "code": extract_method(source, "write_bootstrap"),
        },
        {
            "id": "cpu",
            "title": ".asm → CPUEmulator",
            "icon": "🖥️",
            "desc": (
                "產出的 .asm 直接餵給官方 CPUEmulator.bat 執行，"
                "由 .tst 腳本描述初始 RAM、ticktock 幾個 cycle，"
                "最後把關鍵記憶體位置輸出成 .out 並與官方 .cmp 比對。"
                "本專案的驗收結果：07/ 5 支、08/ 6 支，共 11 支全部 "
                "'End of script - Comparison ended successfully'。"
            ),
            "code": "# CPUEmulator.bat <test>.tst\n"
            "# 輸出範例（07/MemoryAccess/PointerTest）：\n"
            "#   End of script - Comparison ended successfully\n"
            "#   |RAM[256]| RAM[3] | RAM[4] |RAM[3032|RAM[3046|\n"
            "#   |   6084 |   3030 |   3040 |     32 |     46 |\n"
            "#\n"
            "# 執行方式：\n"
            "#   python 07/vm_translator.py 07/MemoryAccess/BasicTest/BasicTest.vm\n"
            "#   CPUEmulator.bat 07/MemoryAccess/BasicTest/BasicTest.tst",
        },
    ]


# ==========================================================================
# SECTION 4 — the JavaScript VM engine (shared by the page and the verifier)
# ==========================================================================

JS_ENGINE = r"""
/* =====================================================================
   VM Engine — 純 JavaScript 實作的輕量 Hack VM 模擬器
   語意完全對應 08/vm_translator.py 產生的 Hack 組語：
     - if-goto 會彈出被測值（官方 VM Emulator 語意）
     - call 依序推入 retAddr / saved LCL / saved ARG / saved THIS / saved THAT
     - return 先讀 *(frame-5) 再做 *ARG = pop()，最後還原四個指標
   ===================================================================== */
var VMEngine = (function () {
  'use strict';

  function s16(v) {
    v = v & 0xffff;
    return v >= 0x8000 ? v - 0x10000 : v;
  }

  function create(caseData) {
    var ins = caseData.instructions || [];
    var mem = Object.create(null);
    var touched = Object.create(null);
    var stack = [];              /* [{v, tag, pre}]  index 0 = RAM[256] */
    var frames = [];             /* call frames */
    var staticMap = Object.create(null);
    var staticNext = 16;
    var pc = 0;
    var steps = 0;
    var note = '';

    /* ---- 初始記憶體（來自官方 .tst 的 set RAM[..]） ---- */
    var init = caseData.initial || {};
    Object.keys(init).forEach(function (key) {
      var addr = parseInt(key, 10);
      mem[addr] = init[key];
      touched[addr] = 1;
    });
    var sp = (mem[0] === undefined) ? 256 : mem[0];
    for (var a = 256; a < sp; a++) {
      stack.push({ v: mem[a] === undefined ? 0 : mem[a],
                   tag: 'pre', pre: mem[a] !== undefined });
    }

    function getMem(addr) { return mem[addr] === undefined ? 0 : mem[addr]; }
    function setMem(addr, v) { mem[addr] = s16(v); touched[addr] = 1; }

    function push(v, tag) {
      var addr = getMem(0);
      var val = s16(v);
      mem[addr] = val; touched[addr] = 1;
      stack.push({ v: val, tag: tag || 'value', pre: false });
      setMem(0, addr + 1);
      return val;
    }
    function pop() {
      var addr = getMem(0) - 1;
      setMem(0, addr);
      stack.pop();
      /* 回傳記憶體中的實際值：return 會把回傳值寫回 *ARG（參數槽），
         堆疊陣列裡的舊值已經過期，不能直接使用。 */
      return getMem(addr);
    }
    function tagAt(addr) {
      var item = stack[addr - 256];
      return item ? item.tag : 'value';
    }

    function segAddr(seg, i, fileIdx) {
      switch (seg) {
        case 'local': return getMem(1) + i;
        case 'argument': return getMem(2) + i;
        case 'this': return getMem(3) + i;
        case 'that': return getMem(4) + i;
        case 'temp': return 5 + i;
        case 'pointer': return 3 + i;
        case 'static': {
          var key = fileIdx + '.' + i;
          if (staticMap[key] === undefined) { staticMap[key] = staticNext++; }
          return staticMap[key];
        }
      }
      throw new Error('unknown segment: ' + seg);
    }

    function frameIndexByName(name) {
      for (var i = 0; i < frames.length; i++) { if (frames[i].name === name) return i; }
      return -1;
    }

    function exec(instruction) {
      var cmd = instruction.cmd;
      var a1 = instruction.a1;
      var a2 = instruction.a2;
      var i;

      if (cmd === 'push') {
        var idx = parseInt(a2, 10);
        var value = (a1 === 'constant') ? idx : getMem(segAddr(a1, idx, instruction.f));
        push(value, 'value');
        note = '把 ' + (a1 === 'constant' ? '常數 ' + idx : a1 + '[' + idx + '] = ' + value) + ' 壓入堆疊頂端。';
        pc++;
      } else if (cmd === 'pop') {
        var pidx = parseInt(a2, 10);
        var popped = pop();
        setMem(segAddr(a1, pidx, instruction.f), popped);
        note = '彈出堆疊頂端（' + popped + '）寫入 ' + a1 + '[' + pidx + ']。';
        pc++;
      } else if (cmd === 'add' || cmd === 'sub' || cmd === 'and' || cmd === 'or') {
        var y = pop(), x = pop(), r;
        if (cmd === 'add') r = x + y;
        else if (cmd === 'sub') r = x - y;
        else if (cmd === 'and') r = x & y;
        else r = x | y;
        push(r, 'value');
        note = cmd + '：x=' + x + ' op y=' + y + ' → ' + s16(r) + '（x 先推入，y 在頂端）。';
        pc++;
      } else if (cmd === 'neg' || cmd === 'not') {
        var v1 = pop();
        var r1 = (cmd === 'neg') ? -v1 : ~v1;
        push(r1, 'value');
        note = cmd + '：' + v1 + ' → ' + s16(r1) + '。';
        pc++;
      } else if (cmd === 'eq' || cmd === 'gt' || cmd === 'lt') {
        var y2 = pop(), x2 = pop();
        var res = (cmd === 'eq') ? (x2 === y2) : (cmd === 'gt' ? x2 > y2 : x2 < y2);
        push(res ? -1 : 0, 'value');
        note = cmd + '：x=' + x2 + ', y=' + y2 + ' → ' + (res ? '-1 (0xFFFF, 真)' : '0 (假)');
        pc++;
      } else if (cmd === 'label') {
        note = '標記（label）' + (instruction.scope ? instruction.scope + '$' : '') + a1 + '。';
        pc++;
      } else if (cmd === 'goto') {
        note = '無條件跳到 ' + (instruction.scope ? instruction.scope + '$' : '') + a1 + '。';
        pc = instruction.target;
      } else if (cmd === 'if-goto') {
        var cond = pop();
        var taken = (cond !== 0);
        note = 'if-goto：彈出條件值 ' + cond + (taken ? '（非 0）→ 跳轉到 ' : '（0）→ 不跳轉，繼續下一行') +
               (instruction.scope ? instruction.scope + '$' : '') + a1 + '。';
        pc = taken ? instruction.target : pc + 1;
      } else if (cmd === 'function') {
        var k = parseInt(a2, 10);
        var fi = frames.length - 1;
        if (fi >= 0 && frames[fi].expect === a1) {
          frames[fi].localAddr = getMem(0);
          frames[fi].localCount = k;
          frames[fi].expect = null;
        } else {
          frames.push({ name: a1, argAddr: getMem(2), argCount: instruction.argHint || 0,
                        savedAddr: null, localAddr: getMem(0), localCount: k,
                        expect: null, depth: frames.length, standalone: true });
        }
        for (i = 0; i < k; i++) { push(0, 'local'); }
        note = '進入函式 ' + a1 + '：配置 ' + k + ' 個 local（初值 0）。';
        pc++;
      } else if (cmd === 'call') {
        var n = parseInt(a2, 10);
        var argAddr = getMem(0) - n;
        for (i = 0; i < n; i++) {
          var slot = stack[argAddr - 256 + i];
          if (slot) { slot.tag = 'arg'; }
        }
        push(pc + 1, 'ret');
        var savedAddr = getMem(0);
        push(getMem(1), 'savedLCL');
        push(getMem(2), 'savedARG');
        push(getMem(3), 'savedTHIS');
        push(getMem(4), 'savedTHAT');
        setMem(2, getMem(0) - 5 - n);
        setMem(1, getMem(0));
        frames.push({ name: a1, argAddr: argAddr, argCount: n, savedAddr: savedAddr,
                      localAddr: getMem(0), localCount: 0, expect: a1, depth: frames.length });
        note = '呼叫 ' + a1 + '（' + n + ' 個參數）：推入返回位址與 saved LCL/ARG/THIS/THAT，' +
               'ARG = SP-5-' + n + ' = ' + getMem(2) + '，LCL = SP = ' + getMem(1) + '。';
        pc = instruction.target;
      } else if (cmd === 'return') {
        var frame = getMem(1);
        var ret = getMem(frame - 5);
        var value2 = pop();
        setMem(getMem(2), value2);
        setMem(0, getMem(2) + 1);
        setMem(4, getMem(frame - 1));
        setMem(3, getMem(frame - 2));
        setMem(2, getMem(frame - 3));
        setMem(1, getMem(frame - 4));
        stack.length = getMem(0) - 256;
        frames.pop();
        note = 'return：R14 先保存 *(frame-5)=' + ret + '，再 *ARG = pop()（回傳值 ' + value2 + '），' +
               'SP = ARG+1 = ' + getMem(0) + '，並還原 THAT/THIS/ARG/LCL。';
        pc = ret;
      } else {
        throw new Error('unknown command: ' + cmd);
      }
    }

    function bootstrap() {
      setMem(0, 256);
      push(caseData.bootstrapRetIndex, 'ret');
      var savedAddr = getMem(0);
      push(getMem(1), 'savedLCL');
      push(getMem(2), 'savedARG');
      push(getMem(3), 'savedTHIS');
      push(getMem(4), 'savedTHAT');
      setMem(2, getMem(0) - 5);
      setMem(1, getMem(0));
      frames.push({ name: 'Sys.init', argAddr: 256, argCount: 0, savedAddr: savedAddr,
                    localAddr: getMem(0), localCount: 0, expect: 'Sys.init', depth: 0 });
      note = 'bootstrap：SP = 256，等同 call Sys.init 0（推入返回位址與四個 saved 指標，ARG=' + getMem(2) + '，LCL=' + getMem(1) + '），跳進 Sys.init。';
      pc = caseData.bootstrapTargetIndex;
    }

    function snapshot(executed) {
      var regs = { sp: getMem(0), lcl: getMem(1), arg: getMem(2), this: getMem(3), that: getMem(4) };
      var touchedList = Object.keys(touched).map(Number).sort(function (a, b) { return a - b; });
      return {
        step: steps,
        pc: pc,
        ins: executed || null,          /* 剛剛執行完的那一行 VM 指令 */
        next: ins[pc] || null,          /* 下一行即將執行的指令 */
        regs: regs,
        stack: stack.map(function (s, i) {
          /* 顯示用的值一律取自記憶體，確保與 return 覆寫 *ARG 之後一致 */
          var live = mem[256 + i];
          return { v: (live === undefined ? s.v : live), tag: s.tag, pre: !!s.pre };
        }),
        frames: frames.map(function (f) {
          return { name: f.name, argAddr: f.argAddr, argCount: f.argCount,
                   savedAddr: f.savedAddr, localAddr: f.localAddr, localCount: f.localCount,
                   depth: f.depth, standalone: !!f.standalone };
        }),
        mem: Object.assign(Object.create(null), mem),
        touched: touchedList,
        note: note
      };
    }

    function run(limit) {
      var snapshots = [snapshot(null)];
      var visit = Object.create(null);
      var halted = false;
      var haltReason = '';
      if (caseData.bootstrap) { bootstrap(); snapshots.push(snapshot(null)); }
      while (steps < limit) {
        if (pc < 0 || pc >= ins.length) { halted = true; haltReason = '程式執行結束（PC 超出程式範圍）。'; break; }
        var key = pc + '|' + getMem(0);
        visit[key] = (visit[key] || 0) + 1;
        if (visit[key] > 6) { halted = true; haltReason = '偵測到無窮迴圈，模擬已停止（狀態已穩定）。'; break; }
        var executed = ins[pc];
        exec(executed);
        steps++;
        snapshots.push(snapshot(executed));
        if (halted) break;
      }
      return { snapshots: snapshots, halted: halted, haltReason: haltReason, steps: steps };
    }

    return { run: run, mem: mem };
  }

  return { create: create, s16: s16 };
})();
if (typeof module !== 'undefined' && module.exports) { module.exports = VMEngine; }
"""


# ==========================================================================
# SECTION 5 — the page (inline CSS + inline JavaScript)
# ==========================================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nand2Tetris Chapter 08 · VM Translator 互動教學</title>
<style>
:root{
  --bg:#0d1117; --bg2:#161b26; --bg3:#1d2331; --line:#2a3242;
  --fg:#e6e9f2; --fg2:#9aa4b8; --fg3:#6b7688;
  --accent:#7c9cff; --accent2:#4ecdc4; --warn:#ffb454; --danger:#ff6b6b;
  --arg:#7c9cff; --ret:#ffb454; --lcl:#4ecdc4; --work:#c792ea; --pre:#5a6478;
  --mono: ui-monospace, "Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--fg);
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,-apple-system,sans-serif;
  font-size:14px; line-height:1.6;
}
code,pre,.mono{font-family:var(--mono)}
h1,h2,h3,h4{margin:0;font-weight:700;letter-spacing:.01em}

/* ---------- top bar ---------- */
.topbar{
  position:sticky; top:0; z-index:50; background:rgba(13,17,23,.92);
  backdrop-filter:blur(8px); border-bottom:1px solid var(--line);
}
.topbar-inner{max-width:1680px;margin:0 auto;padding:12px 20px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
.logo{
  width:42px;height:42px;border-radius:11px;display:grid;place-items:center;font-weight:800;
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#0d1117;font-size:15px;flex:none;
}
.brand h1{font-size:16px}
.brand p{margin:2px 0 0;color:var(--fg3);font-size:12px}
.tabs{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap}
.tab{
  border:1px solid var(--line);background:var(--bg2);color:var(--fg2);
  padding:8px 16px;border-radius:9px;cursor:pointer;font-size:13px;font-weight:600;
  transition:.16s;font-family:inherit;
}
.tab:hover{background:var(--bg3);color:var(--fg)}
.tab.active{background:linear-gradient(135deg,var(--accent),#6d8bff);color:#0b1020;border-color:transparent}
main{max-width:1680px;margin:0 auto;padding:20px}
.hidden{display:none !important}
.panel{background:var(--bg2);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.panel-head{
  padding:12px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;
  background:linear-gradient(180deg,rgba(255,255,255,.03),transparent);
}
.panel-head h3{font-size:13.5px}
.panel-head .hint{color:var(--fg3);font-size:11.5px;margin-left:auto}
.panel-body{padding:16px}

/* ---------- tab 1 : architecture ---------- */
.pipeline{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;margin-bottom:18px}
.node{
  flex:1 1 170px;min-width:170px;background:var(--bg3);border:1px solid var(--line);
  border-radius:12px;padding:14px;cursor:pointer;transition:.16s;position:relative;
}
.node:hover{border-color:var(--accent);transform:translateY(-2px)}
.node.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.node .ico{font-size:20px}
.node h4{margin:6px 0 4px;font-size:13.5px}
.node p{margin:0;color:var(--fg3);font-size:11.5px;line-height:1.5}
.arrow{display:grid;place-items:center;color:var(--fg3);font-size:18px;padding:0 8px;flex:0 0 auto}
.arch-grid{display:grid;grid-template-columns:minmax(280px,360px) 1fr;gap:16px}
@media(max-width:900px){.arch-grid{grid-template-columns:1fr}}
pre.code{
  margin:0;background:#0a0e14;border:1px solid var(--line);border-radius:10px;padding:14px;
  overflow:auto;max-height:70vh;font-size:12px;line-height:1.65;color:#cfd6e4;
}
pre.code .kw{color:#c792ea}.tk{color:#7c9cff}.str{color:#c3e88d}.cm{color:#5a6478;font-style:italic}
.tagline{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.chip{
  display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
  background:var(--bg3);border:1px solid var(--line);font-size:11.5px;color:var(--fg2);
}
.chip b{color:var(--fg)}

/* ---------- tab 2 : simulator ---------- */
.casebar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.case-btn{
  border:1px solid var(--line);background:var(--bg2);color:var(--fg2);border-radius:10px;
  padding:9px 14px;cursor:pointer;text-align:left;font-family:inherit;transition:.16s;
}
.case-btn:hover{background:var(--bg3);color:var(--fg)}
.case-btn.active{border-color:var(--accent);background:rgba(124,156,255,.12);color:var(--fg)}
.case-btn b{display:block;font-size:13px}
.case-btn span{font-size:11px;color:var(--fg3)}
.ctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:auto}
.btn{
  border:1px solid var(--line);background:var(--bg3);color:var(--fg);border-radius:9px;
  padding:7px 13px;cursor:pointer;font-size:12.5px;font-family:inherit;transition:.14s;
}
.btn:hover:not(:disabled){border-color:var(--accent);background:#232b3b}
.btn:disabled{opacity:.35;cursor:not-allowed}
.btn.primary{background:linear-gradient(135deg,var(--accent),#6d8bff);color:#0b1020;border-color:transparent;font-weight:700}
.stepinfo{font-size:12px;color:var(--fg2);min-width:150px;text-align:right}
input[type=range]{width:240px;accent-color:var(--accent)}

.grid3{display:grid;grid-template-columns:minmax(300px,1.05fr) minmax(340px,1.25fr) minmax(300px,1.1fr);gap:14px;align-items:start}
@media(max-width:1280px){.grid3{grid-template-columns:1fr}}
.col{background:var(--bg2);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.col-head{padding:10px 14px;border-bottom:1px solid var(--line);font-size:12.5px;font-weight:700;color:var(--fg);display:flex;align-items:center;gap:8px}
.col-head .sub{font-weight:400;color:var(--fg3);font-size:11px;margin-left:auto}
.col-body{padding:12px;overflow:auto;max-height:74vh}

/* source view */
.filegroup{margin-bottom:14px}
.filegroup h5{margin:0 0 6px;font-size:11.5px;color:var(--accent2);font-family:var(--mono);font-weight:600}
.srcline{
  display:block;width:100%;text-align:left;background:none;border:0;color:var(--fg2);
  font-family:var(--mono);font-size:11.5px;padding:1px 8px;border-radius:5px;cursor:pointer;
  white-space:pre;border-left:2px solid transparent;
}
.srcline:hover{background:var(--bg3);color:var(--fg)}
.srcline.cur{background:rgba(124,156,255,.16);border-left-color:var(--accent);color:#fff;font-weight:600}
.srcline.cmt{color:var(--fg3);font-style:italic;cursor:default}

/* registers */
.regs{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px}
@media(max-width:520px){.regs{grid-template-columns:repeat(3,1fr)}}
.reg{background:var(--bg3);border:1px solid var(--line);border-radius:10px;padding:8px 10px;transition:.25s}
.reg .n{font-size:10.5px;color:var(--fg3);font-family:var(--mono);letter-spacing:.05em}
.reg .v{font-size:16px;font-weight:700;font-family:var(--mono);color:var(--fg)}
.reg.changed{border-color:var(--accent2);background:rgba(78,205,196,.13);box-shadow:0 0 0 1px rgba(78,205,196,.35)}
.reg .delta{font-size:10px;color:var(--accent2);font-family:var(--mono)}

/* stack view */
.stackview{display:flex;flex-direction:column;gap:2px;font-family:var(--mono);font-size:11.5px}
.slot{
  display:grid;grid-template-columns:52px 1fr 86px 74px;gap:8px;align-items:center;
  padding:4px 8px;border-radius:6px;background:var(--bg3);border:1px solid transparent;
}
.slot .addr{color:var(--fg3)}
.slot .val{text-align:right;font-weight:600}
.slot .lbl{color:var(--fg3);font-size:10.5px}
.slot.tag-arg{background:rgba(124,156,255,.14);border-color:rgba(124,156,255,.35)}
.slot.tag-arg .lbl{color:var(--arg)}
.slot.tag-ret{background:rgba(255,180,84,.16);border-color:rgba(255,180,84,.4)}
.slot.tag-ret .lbl{color:var(--warn)}
.slot.tag-savedLCL,.slot.tag-savedARG,.slot.tag-savedTHIS,.slot.tag-savedTHAT{background:rgba(255,180,84,.09);border-color:rgba(255,180,84,.22)}
.slot.tag-savedLCL .lbl,.slot.tag-savedARG .lbl,.slot.tag-savedTHIS .lbl,.slot.tag-savedTHAT .lbl{color:var(--warn)}
.slot.tag-local{background:rgba(78,205,196,.13);border-color:rgba(78,205,196,.32)}
.slot.tag-local .lbl{color:var(--accent2)}
.slot.tag-value{background:rgba(199,146,234,.1);border-color:rgba(199,146,234,.25)}
.slot.tag-value .lbl{color:var(--work)}
.slot.tag-pre{background:rgba(90,100,120,.13);border-color:rgba(90,100,120,.3)}
.slot.gap{background:transparent;color:var(--fg3);justify-content:center;font-size:10.5px;letter-spacing:.1em}
.framehdr{
  margin:10px 0 4px;padding:7px 10px;border-radius:8px;font-size:11.5px;font-weight:700;
  background:linear-gradient(90deg,rgba(124,156,255,.2),transparent);border-left:3px solid var(--accent);
  display:flex;align-items:center;gap:8px;
}
.framehdr .ptr{margin-left:auto;font-weight:400;color:var(--fg2);font-size:10.5px}
.note{background:var(--bg3);border-left:3px solid var(--accent2);border-radius:8px;padding:9px 12px;font-size:12px;color:var(--fg2);margin-bottom:12px}

/* asm view */
.asmline{display:block;font-family:var(--mono);font-size:11.5px;padding:0 8px;white-space:pre;border-left:2px solid transparent}
.asmline.cmt{color:var(--fg3);font-style:italic}
.asmline.ins{color:#cfd6e4}
.asmline.lbl{color:var(--accent2);font-weight:700}
.explain{background:var(--bg3);border-radius:9px;padding:11px 13px;font-size:12px;color:var(--fg2);margin-top:12px}
.explain b{color:var(--fg)}

/* ---------- AI assistant ---------- */
.ai-grid{display:grid;grid-template-columns:minmax(260px,340px) 1fr;gap:16px}
@media(max-width:900px){.ai-grid{grid-template-columns:1fr}}
.angles{display:flex;flex-direction:column;gap:9px}
.angle{
  text-align:left;border:1px solid var(--line);background:var(--bg3);color:var(--fg2);
  border-radius:10px;padding:11px 13px;cursor:pointer;font-family:inherit;font-size:12.5px;transition:.15s;
}
.angle:hover{border-color:var(--accent);color:var(--fg);background:#232b3b}
.angle b{display:block;color:var(--fg);font-size:12.5px;margin-bottom:2px}
.angle span{font-size:11px;color:var(--fg3)}
.preview{
  background:#0a0e14;border:1px solid var(--line);border-radius:10px;padding:14px;
  font-family:var(--mono);font-size:11.5px;line-height:1.7;white-space:pre-wrap;word-break:break-word;
  color:#cfd6e4;max-height:56vh;overflow:auto;
}
.copyrow{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.status{font-size:12px;color:var(--accent2)}
.badge{font-size:11px;color:var(--fg3);border:1px solid var(--line);border-radius:999px;padding:3px 9px}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--fg3);margin-top:10px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="logo">N2T</div>
    <div class="brand">
      <h1>Nand2Tetris Chapter 08 · VM Translator 互動教學</h1>
      <p>單一 HTML 檔 · 零框架 · 所有組語皆由 <code>08/vm_translator.py</code> 真實翻譯產生</p>
    </div>
    <nav class="tabs">
      <button class="tab" data-tab="arch">① 架構總覽</button>
      <button class="tab active" data-tab="sim">② 互動模擬器</button>
      <button class="tab" data-tab="ai">③ AI 學習助教</button>
    </nav>
  </div>
</header>

<main>
  <!-- ===================== TAB 1 ===================== -->
  <section id="tab-arch" class="hidden">
    <div class="panel" style="margin-bottom:16px">
      <div class="panel-head"><h3>工具鏈流程</h3><span class="hint">點擊任一節點展開真實實作</span></div>
      <div class="panel-body">
        <div class="pipeline" id="pipeline"></div>
        <div class="arch-grid">
          <div class="panel" style="background:var(--bg3)">
            <div class="panel-head"><h3 id="arch-title">節點</h3></div>
            <div class="panel-body">
              <p id="arch-desc" style="color:var(--fg2);font-size:12.5px;margin:0 0 12px"></p>
              <div class="tagline" id="arch-tags"></div>
            </div>
          </div>
          <div>
            <div class="panel" style="margin-bottom:12px">
              <div class="panel-head"><h3>真實程式碼</h3><span class="hint">直接摘自 08/vm_translator.py</span></div>
              <div class="panel-body" style="padding:0"><pre class="code" id="arch-code"></pre></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===================== TAB 2 ===================== -->
  <section id="tab-sim">
    <div class="casebar">
      <div id="case-btns" style="display:flex;gap:10px;flex-wrap:wrap"></div>
      <div class="ctrl">
        <button class="btn" id="btn-first">⏮ 起點</button>
        <button class="btn" id="btn-prev">◀ 上一步</button>
        <button class="btn primary" id="btn-play">▶ 自動播放</button>
        <button class="btn" id="btn-next">下一步 ▶</button>
        <button class="btn" id="btn-last">終點 ⏭</button>
        <button class="btn" id="btn-reset">⟲ 重置</button>
        <input type="range" id="slider" min="0" max="0" value="0">
        <span class="stepinfo" id="stepinfo"></span>
      </div>
    </div>

    <div class="grid3">
      <!-- left : vm source -->
      <div class="col">
        <div class="col-head">① <span>.vm 原始碼</span><span class="sub">點擊任一行看說明</span></div>
        <div class="col-body" id="src"></div>
      </div>
      <!-- middle : registers + stack -->
      <div class="col">
        <div class="col-head">② 暫存器 &amp; Stack Frame<span class="sub" id="stackinfo"></span></div>
        <div class="col-body" id="stack"></div>
      </div>
      <!-- right : assembly -->
      <div class="col">
        <div class="col-head">③ 真實 Hack 組語<span class="sub">由 vm_translator.py 產生</span></div>
        <div class="col-body" id="asm"></div>
      </div>
    </div>

    <div style="margin-top:16px" id="ai-slot-sim"></div>
  </section>

  <!-- ===================== TAB 3 ===================== -->
  <section id="tab-ai" class="hidden">
    <div class="panel">
      <div class="panel-head"><h3>零後端 AI 學習助教</h3><span class="hint">整理成結構化 prompt，複製到任何 AI 工具</span></div>
      <div class="panel-body">
        <div class="ai-grid">
          <div>
            <div class="angles" id="angles"></div>
            <div class="legend">
              <span><i style="background:var(--arg)"></i>ARG 區</span>
              <span><i style="background:var(--ret)"></i>Saved Frame</span>
              <span><i style="background:var(--lcl)"></i>LCL 區</span>
              <span><i style="background:var(--work)"></i>工作堆疊</span>
              <span><i style="background:var(--pre)"></i>.tst 預設值</span>
            </div>
          </div>
          <div>
            <div class="copyrow">
              <button class="btn primary" id="btn-copy">📋 複製目前狀態的 Prompt</button>
              <span class="status" id="copy-status"></span>
              <span class="badge" id="prompt-meta"></span>
            </div>
            <pre class="preview" id="prompt-preview"></pre>
          </div>
        </div>
      </div>
    </div>
  </section>
</main>

<script id="vm-data" type="application/json">/*__VM_DATA__*/</script>
<script>/*__VM_ENGINE__*/</script>
<script>
/* =====================================================================
   互動教學 App — 純 Vanilla JS，無任何外部相依
   ===================================================================== */
(function () {
  'use strict';

  var DATA = JSON.parse(document.getElementById('vm-data').textContent);
  var $ = function (id) { return document.getElementById(id); };

  /* ---------------- tab 切換 ---------------- */
  var currentTab = 'sim';
  function showTab(name) {
    currentTab = name;
    ['arch', 'sim', 'ai'].forEach(function (t) {
      $('tab-' + t).classList.toggle('hidden', t !== name);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === name);
    });
    if (name === 'ai') {
      $('ai-slot-sim').classList.remove('hidden');
      $('tab-ai').insertBefore($('ai-slot-sim'), $('tab-ai').firstChild);
    } else {
      $('tab-sim').appendChild($('ai-slot-sim'));
    }
    if (name === 'sim') { render(); }
  }
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (b) {
    b.addEventListener('click', function () { showTab(b.getAttribute('data-tab')); });
  });

  /* ---------------- Tab 1 : 架構總覽 ---------------- */
  var archNodes = DATA.architecture;
  function renderArchitecture() {
    var pipe = $('pipeline');
    pipe.innerHTML = '';
    archNodes.forEach(function (node, i) {
      var div = document.createElement('div');
      div.className = 'node';
      div.setAttribute('data-id', node.id);
      div.innerHTML = '<div class="ico">' + node.icon + '</div><h4>' + node.title +
                      '</h4><p>' + node.desc.slice(0, 46) + '…</p>';
      div.addEventListener('click', function () { selectArch(node.id); });
      pipe.appendChild(div);
      if (i < archNodes.length - 1) {
        var arr = document.createElement('div');
        arr.className = 'arrow';
        arr.textContent = '→';
        pipe.appendChild(arr);
      }
    });
    selectArch(archNodes[0].id);
  }
  function selectArch(id) {
    var node = null;
    archNodes.forEach(function (n) { if (n.id === id) node = n; });
    Array.prototype.forEach.call(document.querySelectorAll('.node'), function (n) {
      n.classList.toggle('sel', n.getAttribute('data-id') === id);
    });
    $('arch-title').textContent = node.icon + ' ' + node.title;
    $('arch-desc').textContent = node.desc;
    $('arch-code').innerHTML = highlight(node.code);
    $('arch-tags').innerHTML = '';
    (node.tags || []).forEach(function (t) {
      var s = document.createElement('span');
      s.className = 'chip';
      s.innerHTML = t;
      $('arch-tags').appendChild(s);
    });
  }
  function highlight(code) {
    return code.split('\n').map(function (line) {
      var esc = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      if (/^\s*#/.test(line)) { return '<span class="cm">' + esc + '</span>'; }
      esc = esc.replace(/(def|class|return|if|else|for|while|in|not|and|or|None|True|False|import|from|as|pass|raise|with|try|except|lambda|yield|global|nonlocal|assert|del|break|continue|elif)\b/g,
                        '<span class="kw">$1</span>');
      esc = esc.replace(/("([^"]*)")/g, '<span class="str">$1</span>');
      esc = esc.replace(/\b(self|vm_path|lineno|cmd_type|segment|index|offset|name|num_locals|num_args|target|ret_name|counter|scope|lines|block|rest|marker|start|command|arg1|arg2|source|memory|labels|values|expected|cells|memory|parts|arg|label|instructions|files|ret_labels)\b/g,
                        '<span class="tk">$1</span>');
      return esc;
    }).join('\n');
  }

  /* ---------------- Tab 2 : 互動模擬器 ---------------- */
  var caseIndex = 0;
  var caseData = DATA.cases[0];
  var vm = null;
  var run = null;
  var step = 0;
  var timer = null;

  function renderCaseButtons() {
    var box = $('case-btns');
    box.innerHTML = '';
    DATA.cases.forEach(function (c, i) {
      var b = document.createElement('button');
      b.className = 'case-btn' + (i === caseIndex ? ' active' : '');
      b.innerHTML = '<b>' + c.title + '</b><span>' + c.subtitle + '</span>';
      b.addEventListener('click', function () {
        caseIndex = i; caseData = DATA.cases[i];
        stopPlay(); loadCase(); renderCaseButtons();
      });
      box.appendChild(b);
    });
  }

  function loadCase() {
    vm = VMEngine.create(caseData);
    run = vm.run(4000);
    step = 0;
    $('slider').max = Math.max(0, run.snapshots.length - 1);
    $('slider').value = 0;
  }

  function snap() { return run.snapshots[Math.min(step, run.snapshots.length - 1)]; }

  function render() {
    if (!run) { return; }
    var s = snap();
    renderSource(s);
    renderStack(s);
    renderAsm(s);
    $('slider').value = step;
    $('stepinfo').textContent = '步驟 ' + step + ' / ' + (run.snapshots.length - 1) +
      (run.haltReason ? '  · ' + run.haltReason : '');
    $('btn-play').textContent = timer ? '⏸ 暫停' : '▶ 自動播放';
    $('btn-prev').disabled = (step <= 0);
    $('btn-next').disabled = (step >= run.snapshots.length - 1);
    $('btn-first').disabled = (step <= 0);
    $('btn-last').disabled = (step >= run.snapshots.length - 1);
    if (currentTab === 'ai') { renderPrompt(); }
  }

  /* ---- 左欄：原始碼 ---- */
  function renderSource(s) {
    var box = $('src');
    box.innerHTML = '';
    var cur = s.ins || s.next;
    caseData.files.forEach(function (file, fi) {
      var g = document.createElement('div');
      g.className = 'filegroup';
      var h = document.createElement('h5');
      h.textContent = '▸ ' + file.name;
      g.appendChild(h);
      file.lines.forEach(function (line, li) {
        var no = li + 1;
        var isCode = line.split('//')[0].trim().length > 0;
        var b = document.createElement('button');
        b.className = 'srcline';
        var show = line.replace(/\t/g, '    ');
        b.textContent = show.length ? show : ' ';
        if (!isCode) {
          b.classList.add('cmt');
        } else {
          b.addEventListener('click', function () { showExplain(fi, no); });
          if (cur && cur.f === fi && cur.ln === no) { b.classList.add('cur'); }
        }
        g.appendChild(b);
      });
      box.appendChild(g);
    });
  }

  function findInstruction(fi, ln) {
    var found = null;
    caseData.instructions.forEach(function (ins, i) {
      if (ins.f === fi && ins.ln === ln) { found = { ins: ins, index: i }; }
    });
    return found;
  }

  function showExplain(fi, ln) {
    var hit = findInstruction(fi, ln);
    var el = document.createElement('div');
    el.className = 'explain';
    if (!hit) {
      el.innerHTML = '<b>此行是註解或空行</b><br>沒有對應的 VM 指令，組語也不會產生任何內容。';
    } else {
      el.innerHTML = '<b>' + hit.ins.text + '</b><br>' + explainInstruction(hit.ins);
    }
    $('asm').insertBefore(el, $('asm').firstChild);
  }

  function explainInstruction(ins) {
    var s = snap();
    var regs = s ? s.regs : {sp:256,lcl:0,arg:0,this:0,that:0};
    var segName = { local:'LCL', argument:'ARG', this:'THIS', that:'THAT', temp:'TEMP', pointer:'pointer', static:'static', constant:'constant' };
    switch (ins.cmd) {
      case 'push':
        if (ins.a1 === 'constant') return '把常數 ' + ins.a2 + ' 壓入堆疊頂端（SP 由 ' + regs.sp + ' 變為 ' + (regs.sp+1) + '）。';
        return '把 ' + ins.a1 + '[' + ins.a2 + '] 的值壓入堆疊頂端。' + ins.a1 + ' 區的基底由 ' +
               (segName[ins.a1] || ins.a1) + ' 指標決定，目前為 ' + regs[({local:'lcl',argument:'arg',this:'this',that:'that'})[ins.a1]] +
               '，故實際位址為基底+' + ins.a2 + '。';
      case 'pop':
        return '彈出堆疊頂端並寫入 ' + ins.a1 + '[' + ins.a2 + ']（基底由 ' + (segName[ins.a1]||ins.a1) + ' 指標決定）。';
      case 'call':
        return '呼叫函式 ' + ins.a1 + '，' + ins.a2 + ' 個參數已在堆疊頂端。' +
               '依序推入返回位址（frame-5）、saved LCL（frame-4）、saved ARG（frame-3）、saved THIS（frame-2）、saved THAT（frame-1），' +
               '再設定 ARG = SP-5-' + ins.a2 + '、LCL = SP 後跳進函式本體。';
      case 'return':
        return '回傳：先把 *(frame-5) 的返回位址存進 R14，再把堆疊頂端寫入 *ARG（nArgs=0 時 ARG 正好指向返回位址，所以順序不能顛倒），' +
               'SP = ARG+1，並還原 THAT/THIS/ARG/LCL 四個指標。';
      case 'function':
        return '宣告函式 ' + ins.a1 + '，配置 ' + ins.a2 + ' 個 local 變數（初值 0）。LCL 由 call 指令負責指向新 frame。';
      case 'label':
        return '標記 ' + (ins.scope ? ins.scope + '$' : '') + ins.a1 + '。在同一函式內的 goto/if-goto 會自動加上 ' +
               (ins.scope ? ins.scope + '$' : '') + ' 前綴。';
      case 'goto': return '無條件跳到 ' + (ins.scope ? ins.scope + '$' : '') + ins.a1 + '。';
      case 'if-goto':
        return '彈出堆疊頂端作為條件值（非 0 則跳轉），跳到 ' + (ins.scope ? ins.scope + '$' : '') + ins.a1 +
               '。注意：官方 VM Emulator 的 if-goto 會把被測值彈出，兩種分支都一樣，所以堆疊保持平衡。';
      case 'eq': return '比較 x 與 y（x 先推入、y 在頂端），相等則壓入 -1 (0xFFFF)，否則壓入 0。';
      case 'gt': return '比較 x 與 y，x > y 則壓入 -1 (0xFFFF)，否則壓入 0。';
      case 'lt': return '比較 x 與 y，x < y 則壓入 -1 (0xFFFF)，否則壓入 0。';
      case 'add': return 'x + y → 壓回堆疊。';
      case 'sub': return 'x - y → 壓回堆疊（x 先推入、y 在頂端）。';
      case 'neg': return '頂端值取負數後壓回堆疊（堆疊深度不變）。';
      case 'and': return 'x & y（位元 AND）→ 壓回堆疊。';
      case 'or': return 'x | y（位元 OR）→ 壓回堆疊。';
      case 'not': return '頂端值取位元 NOT 後壓回堆疊（堆疊深度不變）。';
      default: return '';
    }
  }

  /* ---- 中欄：暫存器 + 堆疊 ---- */
  var REG_NAMES = [['sp', 'SP'], ['lcl', 'LCL'], ['arg', 'ARG'], ['this', 'THIS'], ['that', 'THAT']];

  function renderStack(s) {
    var box = $('stack');
    box.innerHTML = '';
    var note = document.createElement('div');
    note.className = 'note';
    note.textContent = s.note || '（尚未開始執行）';
    box.appendChild(note);

    /* 暫存器列 */
    var regs = document.createElement('div');
    regs.className = 'regs';
    var prev = (step > 0) ? run.snapshots[step - 1].regs : null;
    REG_NAMES.forEach(function (r) {
      var d = document.createElement('div');
      d.className = 'reg';
      var changed = prev && prev[r[0]] !== s.regs[r[0]];
      if (changed) { d.className += ' changed'; }
      d.innerHTML = '<div class="n">' + r[1] + '</div><div class="v">' + s.regs[r[0]] + '</div>' +
                    (changed ? '<div class="delta">' + prev[r[0]] + ' → ' + s.regs[r[0]] + '</div>' : '');
      regs.appendChild(d);
    });
    box.appendChild(regs);

    /* 堆疊 */
    var view = document.createElement('div');
    view.className = 'stackview';
    var stack = s.stack;
    var frames = s.frames.slice();
    var frameAt = {};
    frames.forEach(function (f) { frameAt[f.argAddr] = f; });

    var touched = {};
    (s.touched || []).forEach(function (a) { touched[a] = 1; });

    var addr = s.regs.sp - 1;
    var gapCount = 0;
    function flushGap() {
      if (gapCount > 0) {
        var g = document.createElement('div');
        g.className = 'slot gap';
        g.textContent = '⋯ 未使用的堆疊空間（' + gapCount + ' 格）⋯';
        view.appendChild(g);
        gapCount = 0;
      }
    }
    while (addr >= 256) {
      if (frameAt[addr]) {
        flushGap();
        view.appendChild(frameHeader(frameAt[addr], frames, s));
      }
      var item = stack[addr - 256];
      var pre = item && item.pre;
      if (!item || (!pre && !touched[addr])) { gapCount++; addr--; continue; }
      flushGap();
      view.appendChild(slotRow(addr, item, s, frames));
      addr--;
    }
    flushGap();
    if (!stack.length) {
      var e = document.createElement('div');
      e.className = 'slot gap';
      e.textContent = '（堆疊是空的）';
      view.appendChild(e);
    }
    box.appendChild(view);

    /* 指標說明 */
    var legend = document.createElement('div');
    legend.className = 'explain';
    legend.innerHTML = '<b>目前指標</b><br>' +
      'SP = ' + s.regs.sp + '（下一個空堆疊槽位）　LCL = ' + s.regs.lcl + '（目前 frame 的 local 區起點）<br>' +
      'ARG = ' + s.regs.arg + '（argument 區起點）　THIS = ' + s.regs.this + '　THAT = ' + s.regs.that + '<br>' +
      '呼叫深度：' + frames.length + ' 層' + (frames.length ? '（最上層：' + frames[frames.length-1].name + '）' : '');
    box.appendChild(legend);
    $('stackinfo').textContent = 'SP=' + s.regs.sp + ' · ' + frames.length + ' 層 frame';
  }

  function frameHeader(f, frames, s) {
    var div = document.createElement('div');
    div.className = 'framehdr';
    var depth = frames.indexOf(f);
    div.innerHTML = '🧱 ' + f.name + ' <span style="color:var(--fg3);font-weight:400">（第 ' + (depth+1) + ' 層 frame' +
                    (f.standalone ? ' · 直接進入' : '') + '）</span>' +
                    '<span class="ptr">ARG↓' + f.argAddr + '　LCL↓' + f.localAddr + '　frame=' + f.localAddr + '</span>';
    return div;
  }

  function slotRow(addr, item, s, frames) {
    var tag = item.tag || 'value';
    var lbl = '';
    var frame = null;
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      var top = (i + 1 < frames.length) ? frames[i+1].argAddr - 1 : s.regs.sp - 1;
      if (addr >= f.argAddr && addr <= top) { frame = f; break; }
    }
    if (frame) {
      if (addr >= frame.argAddr && addr < frame.argAddr + frame.argCount) {
        tag = 'arg'; lbl = 'arg ' + (addr - frame.argAddr);
      } else if (frame.savedAddr !== null && addr >= frame.savedAddr && addr < frame.savedAddr + 5) {
        var names = ['retAddr', 'saved LCL', 'saved ARG', 'saved THIS', 'saved THAT'];
        tag = addr === frame.savedAddr ? 'ret' : 'saved' + names[addr - frame.savedAddr].replace('saved ', '');
        lbl = names[addr - frame.savedAddr];
      } else if (addr >= frame.localAddr && addr < frame.localAddr + frame.localCount) {
        tag = 'local'; lbl = 'local ' + (addr - frame.localAddr);
      } else {
        tag = 'value'; lbl = '工作堆疊';
      }
    } else if (tag === 'pre') {
      lbl = '.tst 預設值';
    } else {
      lbl = '工作堆疊';
    }
    var div = document.createElement('div');
    div.className = 'slot tag-' + tag;
    var val = item.v;
    var valText = (typeof val === 'number') ? val : String(val);
    if (tag === 'ret') {
      var nm = caseData.retLabels && caseData.retLabels[String(val)];
      if (nm) { valText = val + ' (' + nm + ')'; }
    }
    div.innerHTML = '<span class="addr">' + addr + '</span>' +
                    '<span class="lbl">' + lbl + '</span>' +
                    '<span class="val">' + valText + '</span>' +
                    '<span class="lbl">' + (addr === s.regs.sp - 1 ? '▲ SP-1' : '') + '</span>';
    return div;
  }

  /* ---- 右欄：真實組語 ---- */
  function renderAsm(s) {
    var box = $('asm');
    box.innerHTML = '';
    var cur = s.ins || s.next;
    if (!cur) {
      var e = document.createElement('div');
      e.className = 'explain';
      e.textContent = '（尚未開始執行）';
      box.appendChild(e);
      return;
    }
    var head = document.createElement('div');
    head.className = 'explain';
    head.innerHTML = '<b>' + cur.text + '</b>　<span style="color:var(--fg3)">' +
                     (caseData.files[cur.f].name + ' 第 ' + cur.ln + ' 行' +
                      (s.ins ? '' : '　· 即將執行')) + '</span><br>' +
                     explainInstruction(cur);
    box.appendChild(head);
    var pre = document.createElement('div');
    pre.style.marginTop = '10px';
    var src = cur.asm || [];
    src.forEach(function (line) {
      var d = document.createElement('span');
      d.className = 'asmline' + (line.trim().startsWith('//') ? ' cmt' : (line.trim().startsWith('(') ? ' lbl' : ' ins'));
      d.textContent = line + '\n';
      pre.appendChild(d);
    });
    box.appendChild(pre);
  }

  /* ---------------- 控制 ---------------- */
  function stopPlay() { if (timer) { clearInterval(timer); timer = null; } }
  function goTo(n) {
    step = Math.max(0, Math.min(run.snapshots.length - 1, n));
    render();
  }
  $('btn-prev').addEventListener('click', function () { stopPlay(); goTo(step - 1); });
  $('btn-next').addEventListener('click', function () { stopPlay(); goTo(step + 1); });
  $('btn-first').addEventListener('click', function () { stopPlay(); goTo(0); });
  $('btn-last').addEventListener('click', function () { stopPlay(); goTo(run.snapshots.length - 1); });
  $('btn-reset').addEventListener('click', function () { stopPlay(); loadCase(); render(); });
  $('btn-play').addEventListener('click', function () {
    if (timer) { stopPlay(); render(); return; }
    timer = setInterval(function () {
      if (step >= run.snapshots.length - 1) { stopPlay(); render(); return; }
      goTo(step + 1);
    }, 420);
    render();
  });
  $('slider').addEventListener('input', function (e) { stopPlay(); goTo(parseInt(e.target.value, 10)); });

  /* ---------------- Tab 3 : AI 助教 ---------------- */
  var ANGLES = [
    { id: 'explain', title: '解釋這行 VM 指令與對應 Hack 組語的細節',
      desc: '逐行對照 .vm 與 .asm，說明暫存器與堆疊的變化' },
    { id: 'frame', title: '幫我檢查目前 Stack Frame 與 SP/LCL/ARG 的位移邏輯',
      desc: '驗證 frame-1..frame-5 的存取是否正確' },
    { id: 'bug', title: '這一步如果寫錯（例如忘記先存 R14）會發生什麼事？',
      desc: '針對常見錯誤做失效分析' }
  ];
  var currentAngle = 'explain';

  function buildAIpanel() {
    var panel = document.createElement('div');
    panel.id = 'ai-panel';
    panel.className = 'panel';
    panel.innerHTML =
      '<div class="panel-head"><h3>🤖 AI 學習助教（零後端）</h3>' +
      '<span class="hint">把當下狀態整理成結構化 prompt，複製後貼到任何 AI 工具</span></div>' +
      '<div class="panel-body"><div class="ai-grid"><div>' +
      '<div class="angles" id="angles"></div>' +
      '<div class="legend"><span><i style="background:var(--arg)"></i>ARG 區</span>' +
      '<span><i style="background:var(--ret)"></i>Saved Frame</span>' +
      '<span><i style="background:var(--lcl)"></i>LCL 區</span>' +
      '<span><i style="background:var(--work)"></i>工作堆疊</span>' +
      '<span><i style="background:var(--pre)"></i>.tst 預設值</span></div>' +
      '</div><div><div class="copyrow">' +
      '<button class="btn primary" id="btn-copy">📋 複製目前狀態的 Prompt</button>' +
      '<span class="status" id="copy-status"></span>' +
      '<span class="badge" id="prompt-meta"></span></div>' +
      '<pre class="preview" id="prompt-preview"></pre></div></div></div>';
    document.body.appendChild(panel);
    $('ai-slot-sim').appendChild(panel);
    renderAngles();
    $('btn-copy').addEventListener('click', copyPrompt);
    renderPrompt();
  }

  function renderAngles() {
    var box = $('angles');
    box.innerHTML = '';
    ANGLES.forEach(function (a) {
      var b = document.createElement('button');
      b.className = 'angle';
      b.innerHTML = '<b>' + a.title + '</b><span>' + a.desc + '</span>';
      b.addEventListener('click', function () {
        currentAngle = a.id;
        renderAngles();
        renderPrompt();
      });
      box.appendChild(b);
    });
  }

  function stackText(s) {
    var lines = [];
    var frames = s.frames;
    for (var i = s.stack.length - 1; i >= 0; i--) {
      var addr = 256 + i;
      var item = s.stack[i];
      if (item.tag === 'pre' && !item.pre) { continue; }
      var lbl = item.tag;
      for (var j = 0; j < frames.length; j++) {
        var f = frames[j];
        var top = (j + 1 < frames.length) ? frames[j+1].argAddr - 1 : s.regs.sp - 1;
        if (addr >= f.argAddr && addr <= top) {
          if (addr >= f.argAddr && addr < f.argAddr + f.argCount) { lbl = 'arg ' + (addr - f.argAddr); }
          else if (f.savedAddr !== null && addr >= f.savedAddr && addr < f.savedAddr + 5) {
            lbl = ['retAddr','saved LCL','saved ARG','saved THIS','saved THAT'][addr - f.savedAddr];
          } else if (addr >= f.localAddr && addr < f.localAddr + f.localCount) {
            lbl = 'local ' + (addr - f.localAddr);
          } else { lbl = '工作堆疊'; }
          break;
        }
      }
      lines.push('RAM[' + addr + '] = ' + item.v + '   (' + lbl + ')');
    }
    if (!lines.length) { lines.push('（堆疊是空的）'); }
    return lines.join('\n');
  }

  function buildPrompt(angle) {
    var s = snap();
    var ins = s.ins || s.next;
    var p = [];
    p.push('我正在學習 Nand2Tetris Chapter 08 的 VM Translator（純 Python 標準庫實作）。');
    p.push('以下是模擬器目前的真實狀態，請用繁體中文回答。');
    p.push('');
    p.push('【案例】' + caseData.title + ' — ' + caseData.subtitle);
    p.push('【目前步驟】' + s.step + (run.haltReason ? '（' + run.haltReason + '）' : ''));
    p.push('');
    p.push('【目前的 VM 指令】' + (s.ins ? '' : '（即將執行）'));
    if (ins) {
      p.push('  檔案：' + caseData.files[ins.f].name + ' 第 ' + ins.ln + ' 行');
      p.push('  指令：' + ins.text);
      p.push('');
      p.push('【這行指令對應的真實 Hack 組語（由 vm_translator.py 產生）】');
      (ins.asm || []).forEach(function (l) { p.push('  ' + l); });
    } else {
      p.push('  （尚未開始執行）');
    }
    p.push('');
    p.push('【當下暫存器】');
    p.push('  SP = ' + s.regs.sp + '   LCL = ' + s.regs.lcl + '   ARG = ' + s.regs.arg +
           '   THIS = ' + s.regs.this + '   THAT = ' + s.regs.that);
    p.push('');
    p.push('【當下堆疊內容（由高至低）】');
    p.push(stackText(s));
    p.push('');
    p.push('【呼叫堆疊（frame）】');
    if (s.frames.length === 0) { p.push('  （尚未進入任何函式）'); }
    s.frames.forEach(function (f, i) {
      p.push('  第 ' + (i+1) + ' 層：' + f.name +
             '　ARG 區起點=' + f.argAddr + '（' + f.argCount + ' 格）' +
             '　Saved Frame 起點=' + f.savedAddr + '（retAddr/LCL/ARG/THIS/THAT）' +
             '　LCL 區起點=' + f.localAddr + '（' + f.localCount + ' 格）');
    });
    p.push('');
    if (angle === 'explain') {
      p.push('【我要你回答】');
      p.push('請解釋上面這一行 VM 指令的語意，以及它對應的 Hack 組語每一行為什麼這樣寫；');
      p.push('並指出 D、A、M 三個暫存器在這些指令中的角色變化。');
    } else if (angle === 'frame') {
      p.push('【我要你回答】');
      p.push('請幫我檢查目前 Stack Frame 與 SP/LCL/ARG 的位移邏輯是否正確：');
      p.push('  1) 呼叫端推入的順序是否為 arg → retAddr(frame-5) → saved LCL(frame-4) → saved ARG(frame-3) → saved THIS(frame-2) → saved THAT(frame-1) → local；');
      p.push('  2) ARG = SP - 5 - nArgs、LCL = SP 的計算是否正確；');
      p.push('  3) return 時是否先保存 *(frame-5) 再做 *ARG = pop()，並正確還原 THAT/THIS/ARG/LCL。');
    } else {
      p.push('【我要你回答】');
      p.push('如果這一步寫錯（例如 return 時忘記先把 *(frame-5) 存進 R14 就直接 *ARG = pop()），');
      p.push('請說明具體會發生什麼：在 nArgs == 0 與 nArgs > 0 兩種情況下，堆疊、返回位址與呼叫端的 ARG/LCL 會如何被破壞？');
      p.push('並說明應該如何在 .asm 的層級避免這個問題。');
    }
    p.push('');
    p.push('【請注意】');
    p.push('  - 若-goto 會彈出被測值（官方 VM Emulator 語意），兩種分支堆疊深度相同。');
    p.push('  - static 區命名為 <檔名>.<index>，pointer 0 = THIS(RAM[3])、pointer 1 = THAT(RAM[4])。');
    return p.join('\n');
  }

  function renderPrompt() {
    var text = buildPrompt(currentAngle);
    var pv = $('prompt-preview');
    if (pv) { pv.textContent = text; }
    var meta = $('prompt-meta');
    if (meta) {
      var s = snap();
      meta.textContent = caseData.title + ' · 步驟 ' + s.step + ' · ' + text.length + ' 字';
    }
  }

  function copyPrompt() {
    var text = buildPrompt(currentAngle);
    var status = $('copy-status');
    var done = function (ok) {
      if (status) { status.textContent = ok ? '✅ 已複製到剪貼簿！' : '⚠️ 自動複製失敗，請手動選取下方文字。'; }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
                                              function () { done(fallbackCopy(text)); });
    } else {
      done(fallbackCopy(text));
    }
  }

  function fallbackCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }

  /* ---------------- 啟動 ---------------- */
  renderArchitecture();
  renderCaseButtons();
  loadCase();
  buildAIpanel();
  render();
  showTab('sim');
})();
</script>
</body>
</html>
"""


# ==========================================================================
# SECTION 6 — building the cases
# ==========================================================================


def build_cases() -> List[dict]:
    """Build the three teaching cases with real translations."""
    cases: List[dict] = []

    specs = [
        {
            "id": "basic-test",
            "title": "BasicTest",
            "subtitle": "Chapter 07 · 8 種記憶體段與指標存取",
            "desc": "理解 local / argument / this / that / temp / pointer / static / constant 八種記憶體段，"
                    "以及 LCL / ARG / THIS / THAT 四個基底指標如何把「基底 + index」換算出實際位址。",
            "vm_paths": [ROOT / "07" / "MemoryAccess" / "BasicTest" / "BasicTest.vm"],
            "tst": ROOT / "07" / "MemoryAccess" / "BasicTest" / "BasicTest.tst",
            "cmp": ROOT / "07" / "MemoryAccess" / "BasicTest" / "BasicTest.cmp",
            "bootstrap": False,
        },
        {
            "id": "simple-function",
            "title": "SimpleFunction",
            "subtitle": "Chapter 08 · 單層 Stack Frame 與 return 還原",
            "desc": "理解 function 如何配置 local、return 如何把回傳值寫回 *ARG，"
                    "以及 THAT/THIS/ARG/LCL 四個指標如何被還原成呼叫端的值。",
            "vm_paths": [ROOT / "08" / "FunctionCalls" / "SimpleFunction" / "SimpleFunction.vm"],
            "tst": ROOT / "08" / "FunctionCalls" / "SimpleFunction" / "SimpleFunction.tst",
            "cmp": ROOT / "08" / "FunctionCalls" / "SimpleFunction" / "SimpleFunction.cmp",
            "bootstrap": False,
        },
        {
            "id": "fibonacci-element",
            "title": "FibonacciElement",
            "subtitle": "Chapter 08 · Bootstrap + 多檔 Sys.vm/Main.vm + 遞迴 Frame 疊加",
            "desc": "理解資料夾模式如何產生 bootstrap（SP=256 並呼叫 Sys.init）、static 區如何依檔名命名，"
                    "以及遞迴呼叫時 Stack Frame 如何一層層疊上去、return 時又如何逐層收掉。",
            "vm_paths": [
                ROOT / "08" / "FunctionCalls" / "FibonacciElement" / "Main.vm",
                ROOT / "08" / "FunctionCalls" / "FibonacciElement" / "Sys.vm",
            ],
            "tst": ROOT / "08" / "FunctionCalls" / "FibonacciElement" / "FibonacciElement.tst",
            "cmp": ROOT / "08" / "FunctionCalls" / "FibonacciElement" / "FibonacciElement.cmp",
            "bootstrap": True,
        },
    ]

    for spec in specs:
        instructions, files, ret_labels = translate_case(spec["vm_paths"], spec["bootstrap"])
        hints = function_argument_hint(instructions)

        # attach the argument hint to every function instruction
        for index, entry in enumerate(instructions):
            if entry["cmd"] == "function":
                entry["argHint"] = hints.get(index, 0)

        # the bootstrap target is the Sys.init function
        bootstrap_target = _function_index(instructions, "Sys.init")
        bootstrap_ret = bootstrap_target

        ret_labels[str(bootstrap_ret)] = "Sys.init$bootstrap"

        expected = parse_cmp(spec["cmp"])
        initial = parse_tst_memory(spec["tst"])

        cases.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "subtitle": spec["subtitle"],
                "desc": spec["desc"],
                "bootstrap": spec["bootstrap"],
                "files": files,
                "instructions": instructions,
                "initial": {str(k): v for k, v in sorted(initial.items())},
                "expected": [{"addr": a, "value": v} for a, v in expected],
                "cmpText": spec["cmp"].read_text(encoding="utf-8"),
                "retLabels": ret_labels,
                "bootstrapTargetIndex": bootstrap_target,
                "bootstrapRetIndex": bootstrap_ret,
            }
        )

    return cases


# ==========================================================================
# SECTION 7 — verifying the JavaScript simulator against the official .cmp
# ==========================================================================

NODE_HARNESS = r"""
const fs = require('fs');
const VMEngine = require('./engine.js');
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = [];
for (const c of data.cases) {
  const vm = VMEngine.create(c);
  const run = vm.run(4000);
  const last = run.snapshots[run.snapshots.length - 1];
  out.push({
    id: c.id,
    steps: run.steps,
    halted: run.halted,
    haltReason: run.haltReason,
    regs: last.regs,
    mem: last.mem,
    expected: c.expected
  });
}
process.stdout.write(JSON.stringify(out));
"""


def verify_with_node(payload: dict) -> int:
    """Run the JavaScript simulator head-less and compare with the .cmp."""
    node = None
    for candidate in ("node", "nodejs"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            node = candidate
            break
        except (OSError, subprocess.CalledProcessError):
            continue
    if node is None:
        print("! 找不到 node，略過 JavaScript 驗證", file=sys.stderr)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "engine.js").write_text(JS_ENGINE, encoding="utf-8")
        (tmp_path / "harness.js").write_text(NODE_HARNESS, encoding="utf-8")
        (tmp_path / "data.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        result = subprocess.run(
            [node, str(tmp_path / "harness.js"), str(tmp_path / "data.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise SystemExit("JavaScript 驗證失敗（node 執行錯誤）")
        results = json.loads(result.stdout)

    failures = 0
    for case in results:
        print(f"\n>> 驗證案例：{case['id']}（{case['steps']} 步，{case['haltReason'] or '正常結束'}）")
        for item in case["expected"]:
            addr = item["addr"]
            want = item["value"]
            got = case["mem"].get(str(addr), case["mem"].get(addr, 0))
            ok = (got == want)
            failures += 0 if ok else 1
            mark = "[OK]" if ok else "[FAIL] 不一致"
            print(f"   RAM[{addr}]  期望 {want:>8}   實際 {got:>8}   {mark}")
    return failures


# ==========================================================================
# SECTION 8 — main
# ==========================================================================


def main(argv: List[str]) -> int:
    # 保留主控台預設編碼（中文 Windows 為 cp950），但避免遇到無法編碼的字元時中斷
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    out_path = HERE / "visualizer.html"
    if len(argv) > 1:
        out_path = Path(argv[1])

    source = (HERE / "vm_translator.py").read_text(encoding="utf-8")
    cases = build_cases()

    payload = {
        "meta": {
            "generator": "08/build_visualizer.py",
            "translator": "08/vm_translator.py",
            "cases": len(cases),
        },
        "architecture": build_architecture(source),
        "cases": cases,
    }

    failures = verify_with_node(payload)
    if failures:
        print(f"\n[X] JavaScript 模擬器有 {failures} 個值與官方 .cmp 不符，已中止建檔。")
        return 1

    data_json = json.dumps(payload, ensure_ascii=False, indent=1)
    data_json = data_json.replace("</", "<\\/")

    html = HTML_TEMPLATE.replace("/*__VM_DATA__*/", data_json)
    html = html.replace("/*__VM_ENGINE__*/", JS_ENGINE)
    out_path.write_text(html, encoding="utf-8")

    size = out_path.stat().st_size
    print(f"\n[OK] 已產生 {out_path}（{size:,} bytes）")
    print(f"  內嵌案例：{', '.join(c['title'] for c in cases)}")
    print(f"  內嵌真實組語區塊：{sum(len(i['asm']) for c in cases for i in c['instructions'])} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
