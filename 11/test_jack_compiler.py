#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nand2Tetris — Chapter 11 test suite for ``jack_compiler.py``
============================================================

The test suite has four parts:

1. **Compilation** – every one of the six official Chapter 11 projects is
   compiled and has to produce one ``.vm`` file per ``.jack`` file without a
   single error.
2. **Static checks** – the generated VM code is inspected for the three
   constructs the specification calls out as the dangerous ones:

   * an array assignment (``let a[i] = b[j]``) has to use ``temp 0`` and it
     has to store the address of the element *after* the value was computed,
   * every ``do`` statement has to discard the value of the call with
     ``pop temp 0``,
   * every ``method`` has to receive its object with ``push argument 0`` /
     ``pop pointer 0``.
3. **Differential test** – if the official Nand2Tetris ``JackCompiler`` can
   be found, the same sources are compiled with it and the two ``.vm`` files
   are compared line by line.
4. **Behaviour test** – if the official ``VMEmulator`` can be found, the
   programs are executed and their results are checked (``Seven`` prints
   ``7``, ``ConvertToBin`` writes the binary representation of a number into
   RAM and ``ComplexArrays`` prints five results).

Usage
-----
    python 11/test_jack_compiler.py
    python 11/test_jack_compiler.py --tools "C:/path/to/nand2tetris/tools"

The directory that holds the official tools is looked up automatically; it
can also be given with the ``--tools`` option or with the ``N2T_TOOLS``
environment variable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import jack_compiler  # noqa: E402  (the path has to be prepared first)


# ==========================================================================
# SECTION 1 — THE TEST DATA
# ==========================================================================

#: The six official Chapter 11 projects.
PROJECTS: Tuple[str, ...] = (
    "Seven",
    "ConvertToBin",
    "Square",
    "Average",
    "Pong",
    "ComplexArrays",
)

#: The projects that are executed in the VM emulator.
BEHAVIOUR_PROJECTS: Tuple[str, ...] = ("Seven", "ConvertToBin", "ComplexArrays")

#: The OS classes of the Nand2Tetris software suite that the behaviour test
#: needs.  They are taken from ``<tools>/OS`` (the OS of the suite, already
#: compiled into VM code).  ``Array``, ``Math`` and ``Memory`` are always
#: needed because the tiny ``Sys`` class initialises them.
NEEDED_OS_CLASSES: Tuple[str, ...] = ("Array", "Math", "Memory")

#: A tiny replacement for the ``Output`` class of the operating system.  It
#: does not draw on the screen (the screen of the VM emulator is not part of
#: its RAM) but records everything that is printed:
#:
#:   RAM[8199]   the number of characters in the text buffer
#:   RAM[8200+]  the characters that were printed
#:   RAM[8599]   the number of integers that were printed
#:   RAM[8600+]  the integers that were printed, in order
MINI_OUTPUT_VM = """\
// A tiny replacement for the Output class: instead of drawing on the screen
// it records everything that is printed into RAM, so that the test script
// can check the result.
function Output.printInt 0
    push constant 8599
    pop pointer 1
    push that 0
    push constant 8600
    add
    pop pointer 1
    push argument 0
    pop that 0
    push constant 8599
    pop pointer 1
    push that 0
    push constant 1
    add
    pop that 0
    push constant 0
    return

function Output.printString 3
    push constant 8199
    pop pointer 1
    push that 0
    pop local 0
    push argument 0
    call String.length 1
    pop local 2
    push local 2
    push constant 1
    sub
    pop local 1
label OUTPUT_LOOP
    push local 1
    push constant 0
    lt
    if-goto OUTPUT_DONE
    push constant 8200
    push local 0
    add
    push local 1
    add
    pop pointer 1
    push argument 0
    push local 1
    call String.charAt 2
    pop that 0
    push local 1
    push constant 1
    sub
    pop local 1
    goto OUTPUT_LOOP
label OUTPUT_DONE
    push constant 8199
    pop pointer 1
    push local 0
    push local 2
    add
    pop that 0
    push constant 0
    return

function Output.println 0
    push constant 8199
    pop pointer 1
    push that 0
    push constant 8200
    add
    pop pointer 1
    push constant 10
    pop that 0
    push constant 8199
    pop pointer 1
    push that 0
    push constant 1
    add
    pop that 0
    push constant 0
    return
"""

#: A tiny replacement for the ``Sys`` class of the operating system.  It
#: initialises the OS classes that are needed and then calls ``Main.main``.
#:
#: The official ``Sys.vm`` cannot be used: the VM emulator resolves the
#: labels of a function while it parses the file and it therefore expects
#: ``Sys.init`` to be the *last* function of the file.
MINI_SYS_VM = """\
// A tiny replacement for the Sys class: it initialises the OS classes and
// then calls Main.main.
function Sys.error 1
    push constant 8598
    pop pointer 1
    push argument 0
    pop that 0
label END_ERROR
    goto END_ERROR

function Sys.halt 0
label END_HALT
    goto END_HALT

function Sys.wait 1
    push constant 0
    return

function Sys.init 0
    call Memory.init 0
    pop temp 0
    call Math.init 0
    pop temp 0
    call Main.main 0
    pop temp 0
label END
    goto END
"""

#: The tail of an array assignment, exactly as the specification demands it.
ARRAY_ASSIGNMENT_TAIL: Tuple[str, ...] = (
    "pop temp 0",
    "pop pointer 1",
    "push temp 0",
    "pop that 0",
)


# ==========================================================================
# SECTION 2 — A SMALL TEST FRAMEWORK
# ==========================================================================


class TestReport:
    """Collects the results of the individual checks."""

    def __init__(self) -> None:
        self.failures: List[str] = []
        self.checks = 0

    def check(self, condition: bool, message: str) -> bool:
        """Record one check; a failed check is remembered."""
        self.checks += 1
        if not condition:
            self.failures.append(message)
        return bool(condition)

    def equal(self, actual, expected, message: str) -> bool:
        """Record one equality check."""
        return self.check(
            actual == expected, f"{message} (expected {expected!r}, got {actual!r})"
        )

    def summary(self) -> str:
        """A short report of the run."""
        if not self.failures:
            return f"OK: {self.checks} checks passed"
        lines = [f"FAILED: {len(self.failures)} of {self.checks} checks failed"]
        lines += [f"  - {failure}" for failure in self.failures]
        return "\n".join(lines)


# ==========================================================================
# SECTION 3 — TRACING THE COMPILER
# ==========================================================================


class TracingWriter(jack_compiler.VMWriter):
    """A :class:`jack_compiler.VMWriter` that also records the constructs."""

    def __init__(self) -> None:
        super().__init__()
        #: (kind, commands) of every statement that was compiled
        self.events: List[Tuple[str, List[str]]] = []
        #: the commands that computed the address of an array element
        self.address_slices: List[List[str]] = []


class TracingEngine(jack_compiler.CompilationEngine):
    """A :class:`jack_compiler.CompilationEngine` that records what it does.

    The recording makes it possible to check the *meaning* of the generated
    code (which statement produced which commands) instead of only looking
    for a pattern somewhere in the file.
    """

    def __init__(self, tokenizer: jack_compiler.JackTokenizer, writer: TracingWriter):
        super().__init__(tokenizer, writer)
        self.writer: TracingWriter = writer  # type: ignore[assignment]

    # -- statements --------------------------------------------------------

    def _compile_do(self) -> None:
        start = self.writer.mark()
        super()._compile_do()
        self.writer.events.append(("do", list(self.writer.commands[start:])))

    def _compile_let(self) -> None:
        # The identifier is the current token; the one behind it tells an
        # array assignment ("let a[i] = ...") from a plain one.
        following = self.tk.peek(1)
        is_array = (
            following is not None
            and following.kind == "symbol"
            and following.value == "["
        )
        n_addresses = len(self.writer.address_slices)
        start = self.writer.mark()
        super()._compile_let()
        commands = list(self.writer.commands[start:])
        address = None
        if is_array and len(self.writer.address_slices) > n_addresses:
            address = self.writer.address_slices[-1]
        self.writer.events.append(("let", commands, address))

    def _compile_array_address(self, name: str) -> None:
        start = self.writer.mark()
        super()._compile_array_address(name)
        self.writer.address_slices.append(list(self.writer.commands[start:]))


def trace_class(jack_path: Path) -> Tuple[List[str], List[Tuple]]:
    """Compile a Jack file and return its VM code and its trace events."""
    text = jack_path.read_text(encoding="utf-8-sig")
    tokenizer = jack_compiler.JackTokenizer(text)
    writer = TracingWriter()
    engine = TracingEngine(tokenizer, writer)
    engine.compile_class()
    return writer.commands, writer.events


# ==========================================================================
# SECTION 4 — PART 1: COMPILATION
# ==========================================================================


def compile_project(project_dir: Path, out_dir: Path) -> List[Path]:
    """Compile every ``.jack`` file of a project into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for jack_path in sorted(project_dir.glob("*.jack")):
        written.append(jack_compiler.compile_file(jack_path))
    return written


# ==========================================================================
# SECTION 5 — PART 2: THE STATIC CHECKS
# ==========================================================================


def check_static_rules(project: str, vm_path: Path, report: TestReport) -> None:
    """Check the three dangerous constructs of the generated VM code."""
    commands, events = trace_class(vm_path.with_suffix(".jack"))

    # -- every "do" statement discards the value of the call --------------
    for index, event in enumerate(events):
        if event[0] != "do":
            continue
        block = event[1]
        report.check(
            len(block) >= 2 and block[-1] == "pop temp 0",
            f"{project}/{vm_path.name}: do #{index + 1} does not end with "
            f"'pop temp 0' (got {block[-1:]})",
        )
        report.check(
            any(command.startswith("call ") for command in block),
            f"{project}/{vm_path.name}: do #{index + 1} does not call anything",
        )
        if len(block) >= 2:
            report.check(
                block[-2].startswith("call "),
                f"{project}/{vm_path.name}: do #{index + 1} is not followed by "
                f"'pop temp 0' (got {block[-2:-1]})",
            )

    # -- every array assignment uses temp 0 in the right order -------------
    for index, event in enumerate(events):
        if event[0] != "let":
            continue
        block, address = event[1], event[2]
        if address is None:
            continue  # a plain "let name = expression;"

        report.check(
            tuple(block[-4:]) == ARRAY_ASSIGNMENT_TAIL,
            f"{project}/{vm_path.name}: array assignment #{index + 1} does not "
            f"end with {list(ARRAY_ASSIGNMENT_TAIL)} (got {block[-4:]})",
        )
        # The address of the element has to survive the evaluation of the
        # value: it stays on the stack, so it must not be stored in THAT
        # before the value has been computed.  The address computation
        # therefore ends with the "add" and not with a "pop pointer 1".
        report.check(
            bool(address) and address[-1] == "add",
            f"{project}/{vm_path.name}: array assignment #{index + 1} does not "
            f"compute the address of the element (got {address[-3:]})",
        )
        report.check(
            not address or address[-1] != "pop pointer 1",
            f"{project}/{vm_path.name}: array assignment #{index + 1} stores the "
            f"address in THAT before the value is computed",
        )

    # -- every method receives its object ---------------------------------
    source = vm_path.with_suffix(".jack").read_text(encoding="utf-8-sig")
    class_name = ""
    methods = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("class "):
            class_name = stripped.split()[1].split("{")[0]
        if not stripped.startswith("method "):
            continue
        # "method <returnType> <name>(...)"
        methods.append(stripped.split()[2].split("(")[0])

    for method in methods:
        header = f"function {class_name}.{method}"
        positions = [
            index
            for index, command in enumerate(commands)
            if command.startswith(header + " ")
        ]
        if not positions:
            report.check(False, f"{project}/{vm_path.name}: {header} is missing")
            continue
        start = positions[0]
        prologue = commands[start + 1 : start + 3]
        report.equal(
            prologue,
            ["push argument 0", "pop pointer 0"],
            f"{project}/{vm_path.name}: the prologue of {method} is wrong",
        )


# ==========================================================================
# SECTION 6 — PART 3: THE OFFICIAL COMPILER
# ==========================================================================


def find_tools(explicit: Optional[str] = None) -> Optional[Path]:
    """Look for the directory that holds the official Nand2Tetris tools."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("N2T_TOOLS")
    if env:
        candidates.append(Path(env))

    repo = HERE.parent
    candidates += [
        repo.parent / "nand2tetris" / "nand2tetris" / "tools",
        repo / "tools",
        Path("C:/nand2tetris/tools"),
    ]

    for candidate in candidates:
        if (candidate / "JackCompiler.bat").is_file() or (
            candidate / "JackCompiler.sh"
        ).is_file():
            return candidate

    # last resort: look for the tools a few levels above the repository
    for root in (repo.parent, repo):
        try:
            for found in root.glob("**/JackCompiler.bat"):
                return found.parent
        except OSError:  # pragma: no cover - unreadable directory
            continue
    return None


def run_official_compiler(tool: Path, source_dir: Path, out_dir: Path) -> bool:
    """Compile a folder with the official ``JackCompiler``."""
    launcher = tool / ("JackCompiler.bat" if os.name == "nt" else "JackCompiler.sh")
    if not launcher.is_file():
        return False

    # The official compiler writes its result next to the sources, so the
    # sources are copied into a scratch folder first.
    scratch = out_dir
    scratch.mkdir(parents=True, exist_ok=True)
    for jack_file in source_dir.glob("*.jack"):
        shutil.copy(jack_file, scratch / jack_file.name)

    try:
        completed = subprocess.run(
            [str(launcher), str(scratch)],
            cwd=str(tool),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    if completed.returncode != 0:
        print("    (the official compiler reported an error)")
        return False
    return any(scratch.glob("*.vm"))


def compare_with_official(
    project: str, source_dir: Path, vm_paths: Sequence[Path], tool: Path, work: Path,
    report: TestReport,
) -> bool:
    """Compile with the official compiler and compare the two results."""
    reference_dir = work / f"official_{project}"
    if not run_official_compiler(tool, source_dir, reference_dir):
        return False

    for vm_path in vm_paths:
        reference = reference_dir / vm_path.name
        if not reference.is_file():
            report.check(False, f"{project}: the official compiler produced no {vm_path.name}")
            continue
        mine = vm_path.read_text(encoding="utf-8").splitlines()
        theirs = reference.read_text(encoding="utf-8").splitlines()
        if mine == theirs:
            report.check(
                True,
                f"{project}/{vm_path.name}: identical to the official compiler",
            )
        else:
            report.check(
                False,
                f"{project}/{vm_path.name}: differs from the official compiler",
            )
            for number, (left, right) in enumerate(zip(mine, theirs), start=1):
                if left != right:
                    report.check(
                        False,
                        f"    line {number}: mine {left!r} / official {right!r}",
                    )
                    break
            if len(mine) != len(theirs):
                report.check(
                    False,
                    f"    the file has {len(mine)} lines, the official one {len(theirs)}",
                )
    return True


# ==========================================================================
# SECTION 7 — PART 4: THE VM EMULATOR
# ==========================================================================


def run_vm_emulator(tool: Path, script: Path, timeout: int = 600) -> Tuple[bool, str]:
    """Run a VM emulator script; returns ``(ok, output)``."""
    launcher = tool / ("VMEmulator.bat" if os.name == "nt" else "VMEmulator.sh")
    if not launcher.is_file():
        return False, "the VM emulator is not available"

    try:
        completed = subprocess.run(
            [str(launcher), str(script)],
            cwd=str(tool),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "the VM emulator timed out"
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)

    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, output


def read_ram(tool: Path, program_dir: Path, addresses: Sequence[int], steps: int) -> Dict[int, int]:
    """Run a program in the VM emulator and read some RAM cells."""
    values: Dict[int, int] = {}
    # The emulator accepts at most a dozen addresses per "output-list".
    for start in range(0, len(addresses), 8):
        chunk = addresses[start : start + 8]
        # The script parser of the emulator does not accept an underscore in
        # a file name.
        script = program_dir / f"vmtest{start}.tst"
        listing = " ".join(f"RAM[{address}]%D6.6.1" for address in chunk)
        script.write_text(
            "load,\n"
            f"output-file vmtest{start}.out,\n"
            f"output-list {listing};\n"
            "\n"
            "set RAM[8000] 12345,\n"
            f"repeat {steps} {{\n  vmstep;\n}}\n"
            "output;\n",
            encoding="ascii",
        )
        ok, output = run_vm_emulator(tool, script)
        if not ok:
            raise RuntimeError(f"the VM emulator failed: {output.strip()}")

        out_file = program_dir / f"vmtest{start}.out"
        rows = out_file.read_text(encoding="utf-8").splitlines()
        if len(rows) < 2:
            raise RuntimeError("the VM emulator produced no output")
        cells = [cell.strip() for cell in rows[1].split("|") if cell.strip()]
        if len(cells) != len(chunk):
            raise RuntimeError("the VM emulator produced an unexpected output")
        for address, cell in zip(chunk, cells):
            values[address] = int(cell)
    return values


def prepare_program(
    project: str, vm_paths: Sequence[Path], tool: Path, work: Path
) -> Path:
    """Build the folder the VM emulator runs: the program, the OS, and a
    tiny ``Sys`` / ``Output`` class."""
    program_dir = work / f"vm_{project}"
    program_dir.mkdir(parents=True, exist_ok=True)

    for vm_path in vm_paths:
        shutil.copy(vm_path, program_dir / vm_path.name)

    # The VM emulator resolves every call of every loaded file, so only the
    # OS classes that are really needed may be copied into the folder.
    program = "".join(vm_path.read_text(encoding="utf-8") for vm_path in vm_paths)
    os_classes = list(NEEDED_OS_CLASSES)
    if "call Output." in program:
        os_classes.append("Output")
    if "call String." in program or "Output" in os_classes:
        os_classes.append("String")

    os_dir = tool / "OS"
    for class_name in os_classes:
        source = os_dir / f"{class_name}.vm"
        if source.is_file():
            shutil.copy(source, program_dir / source.name)

    (program_dir / "Sys.vm").write_text(MINI_SYS_VM, encoding="ascii")
    if "Output" in os_classes:
        (program_dir / "Output.vm").write_text(MINI_OUTPUT_VM, encoding="ascii")
    return program_dir


def check_behaviour(
    project: str, vm_paths: Sequence[Path], tool: Path, work: Path, report: TestReport
) -> None:
    """Run a program in the VM emulator and check what it produced."""
    program_dir = prepare_program(project, vm_paths, tool, work)

    if project == "Seven":
        values = read_ram(tool, program_dir, [8600], steps=2_000_000)
        report.equal(values[8600], 7, "Seven does not print 7")

    elif project == "ConvertToBin":
        values = read_ram(tool, program_dir, list(range(8001, 8017)), steps=2_000_000)
        bits = [values[address] for address in range(8001, 8017)]
        expected = [(12345 >> bit) & 1 for bit in range(16)]
        report.equal(bits, expected, "ConvertToBin writes the wrong bits")

    elif project == "ComplexArrays":
        addresses = list(range(8600, 8605)) + list(range(8200, 8290))
        values = read_ram(tool, program_dir, addresses, steps=2_000_000)
        printed = [values[address] for address in range(8600, 8605)]
        report.equal(
            printed,
            [5, 40, 0, 77, 110],
            "ComplexArrays does not print the expected results",
        )
        text = "".join(chr(values[address]) for address in range(8200, 8290))
        for expected in (
            "Test 1: expected result: 5; actual result: ",
            "Test 2: expected result: 40; actual result: ",
        ):
            report.check(
                expected in text,
                f"ComplexArrays does not print {expected!r} (it prints {text!r})",
            )


# ==========================================================================
# SECTION 8 — MAIN
# ==========================================================================


def main(argv: List[str]) -> int:
    """Run the whole test suite."""
    tools_arg: Optional[str] = None
    if len(argv) == 3 and argv[1] == "--tools":
        tools_arg = argv[2]
    elif len(argv) != 1:
        print("Usage: test_jack_compiler.py [--tools <nand2tetris/tools>]")
        return 1

    report = TestReport()
    tools = find_tools(tools_arg)
    if tools is None:
        print("note: the official Nand2Tetris tools were not found;")
        print("      the differential test and the behaviour test are skipped.")
        print("      (use --tools <directory> or the N2T_TOOLS variable)")
    else:
        print(f"official tools: {tools}")

    work = Path(tempfile.mkdtemp(prefix="jack_test_"))
    try:
        for project in PROJECTS:
            source_dir = HERE / project
            if not source_dir.is_dir():
                report.check(False, f"the project {project} is missing")
                continue

            out_dir = work / project
            print(f"\n== {project} ==")
            try:
                vm_paths = compile_project(source_dir, out_dir)
            except jack_compiler.JackCompilationError as error:
                report.check(False, f"{project}: {error}")
                continue

            jack_files = sorted(source_dir.glob("*.jack"))
            report.equal(
                len(vm_paths),
                len(jack_files),
                f"{project}: the number of compiled files is wrong",
            )
            for vm_path in vm_paths:
                report.check(
                    vm_path.is_file() and vm_path.stat().st_size > 0,
                    f"{project}: {vm_path.name} was not written",
                )
            print(f"   compiled {len(vm_paths)} file(s)")

            for vm_path in vm_paths:
                check_static_rules(project, vm_path, report)
            print("   static checks done")

            if tools is not None:
                if compare_with_official(project, source_dir, vm_paths, tools, work, report):
                    print("   identical to the official compiler")
                else:
                    report.check(False, f"{project}: the official compiler failed")

            if tools is not None and project in BEHAVIOUR_PROJECTS:
                try:
                    check_behaviour(project, vm_paths, tools, work, report)
                    print("   the program behaves correctly")
                except (RuntimeError, OSError) as error:
                    report.check(False, f"{project}: {error}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 70)
    print(report.summary())
    return 0 if not report.failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
