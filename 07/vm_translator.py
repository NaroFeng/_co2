#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nand2Tetris — Chapter 07 VM Translator (Stack Arithmetic & Memory Access)
=========================================================================

A from-scratch implementation written in pure Python 3 (standard library
only: ``sys``, ``pathlib``, ``dataclasses``).

Usage
-----
    python 07/vm_translator.py <path/to/file.vm>

The translator writes the matching Hack assembly file next to the input
(``file.vm`` -> ``file.asm``).

Module layout
-------------
1. ``Parser``      – reads a ``.vm`` file, strips comments/whitespace and
                     turns every meaningful line into a structured
                     :class:`VMCommand` (``cmd_type``, ``arg1``, ``arg2``).
2. ``CodeWriter``  – translates a *single* VM command into Hack assembly.
                     Every translation method returns a ``list[str]`` (the
                     assembly lines for that one command).  The writer
                     collects those blocks and emits the ``.asm`` file,
                     prefixing every block with a ``// <original VM
                     instruction>`` comment.
                     The per-command mapping is exactly what the upcoming
                     visualiser stage needs, so no refactoring is required
                     when that stage arrives.
3. ``main``        – command line handling and file output.

Only Chapter 07 is implemented here (arithmetic + memory access).  The
Chapter 08 commands (``function``/``call``/``return``/``label``/``goto``/
``if-goto``) are recognised by the parser and rejected with a clear message;
they are implemented in the Chapter 08 stage of this project.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

# ==========================================================================
# SECTION 1 — PARSER
# ==========================================================================


@dataclass(frozen=True)
class VMCommand:
    """One structured VM instruction.

    ``cmd_type``  the mnemonic, e.g. ``push``, ``pop``, ``add``.
    ``arg1``      the segment name for ``push``/``pop`` (``""`` otherwise).
    ``arg2``      the index for ``push``/``pop`` (``""`` otherwise).
    ``source``    the original VM instruction (comment stripped); it is
                  emitted as a ``// ...`` comment in front of the generated
                  assembly so the ``.asm`` file stays readable.
    """

    cmd_type: str
    arg1: str = ""
    arg2: str = ""
    source: str = ""


class VMTranslationError(Exception):
    """Raised when a ``.vm`` file cannot be translated."""


class Parser:
    """Turns a ``.vm`` file into a list of :class:`VMCommand` objects."""

    #: commands without arguments
    NO_ARG_CMDS = frozenset(
        {"add", "sub", "neg", "eq", "gt", "lt", "and", "or", "not"}
    )

    #: commands with a segment and an index
    MEMORY_CMDS = frozenset({"push", "pop"})

    #: the memory segments of the Hack architecture (``constant`` is the
    #: pseudo segment used by ``push constant i``)
    SEGMENTS = frozenset(
        {
            "constant",
            "local",
            "argument",
            "this",
            "that",
            "temp",
            "pointer",
            "static",
        }
    )

    #: the pointer segment has exactly two entries: pointer 0 (THIS) and
    #: pointer 1 (THAT)
    POINTER_INDICES = (0, 1)

    #: Chapter 08 commands — handled in the next stage of this project
    CH08_CMDS = frozenset(
        {"function", "call", "return", "label", "goto", "if-goto"}
    )

    def __init__(self, vm_path: Path) -> None:
        self.vm_path = Path(vm_path)

    # -- public API --------------------------------------------------------

    def parse(self) -> List[VMCommand]:
        """Return every meaningful instruction of the ``.vm`` file."""
        if not self.vm_path.is_file():
            raise VMTranslationError(f"VM file not found: {self.vm_path}")

        text = self.vm_path.read_text(encoding="utf-8")
        commands: List[VMCommand] = []

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue  # blank line or comment-only line

            parts = line.split()
            cmd_type = parts[0]
            arg1 = arg2 = ""

            if cmd_type in self.MEMORY_CMDS:
                arg1, arg2 = self._parse_memory_args(parts, lineno)
            elif cmd_type in self.NO_ARG_CMDS:
                pass
            elif cmd_type in self.CH08_CMDS:
                raise VMTranslationError(
                    f"{self.vm_path}:{lineno}: '{cmd_type}' is a Chapter 08 "
                    f"command and is not implemented in this stage"
                )
            else:
                raise VMTranslationError(
                    f"{self.vm_path}:{lineno}: unknown command '{cmd_type}'"
                )

            commands.append(VMCommand(cmd_type, arg1, arg2, line))

        return commands

    # -- helpers -----------------------------------------------------------

    def _parse_memory_args(self, parts: List[str], lineno: int) -> tuple[str, str]:
        """Validate and return ``(segment, index)`` of a push/pop command."""
        if len(parts) != 3:
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: '{parts[0]}' expects a segment and "
                f"an index, e.g. '{parts[0]} local 0'"
            )

        segment, index = parts[1], parts[2]

        if segment not in self.SEGMENTS:
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: unknown segment '{segment}'"
            )

        if parts[0] == "pop" and segment == "constant":
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: 'constant' can only be pushed"
            )

        try:
            value = int(index)
        except ValueError:
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: '{index}' is not a valid index"
            ) from None

        if value < 0:
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: index {value} must not be negative"
            )

        if segment == "pointer" and value not in self.POINTER_INDICES:
            raise VMTranslationError(
                f"{self.vm_path}:{lineno}: the pointer segment only has "
                f"pointer 0 (THIS) and pointer 1 (THAT), got pointer {value}"
            )

        return segment, str(value)


# ==========================================================================
# SECTION 2 — CODE WRITER
# ==========================================================================


@dataclass(frozen=True)
class Translation:
    """One VM command together with the assembly it produced.

    This is the data source the visualiser stage will consume.
    """

    command: VMCommand
    asm: List[str]


class CodeWriter:
    """Translates VM commands into Hack assembly.

    Every ``write_*`` method returns the assembly lines of exactly one VM
    instruction as ``list[str]``; :meth:`write_command` stores them together
    with the original instruction and :meth:`write_output` serialises the
    whole program to a ``.asm`` file.
    """

    #: scratch cell used inside a single VM instruction (never clashes with
    #: the registers used by the Chapter 08 instructions)
    SCRATCH: str = "R13"

    #: segments addressed through a base pointer register
    SEGMENT_POINTERS: Dict[str, str] = {
        "local": "LCL",
        "argument": "ARG",
        "this": "THIS",
        "that": "THAT",
    }

    #: segments with a fixed base address
    SEGMENT_BASES: Dict[str, int] = {
        "temp": 5,      # temp segment   -> RAM[5..12]
        "pointer": 3,   # pointer segment -> RAM[3] (THIS), RAM[4] (THAT)
    }

    #: mnemonic -> name of the CodeWriter method implementing it
    ARITHMETIC_HANDLERS: Dict[str, str] = {
        "add": "add",
        "sub": "sub",
        "neg": "neg",
        "eq": "eq",
        "gt": "gt",
        "lt": "lt",
        "and": "and_",   # 'and' is a Python keyword
        "or": "or_",     # 'or' is a Python keyword
        "not": "not_",   # 'not' is a Python keyword
    }

    def __init__(self, module_name: str = "") -> None:
        #: stem of the ``.vm`` file, used for the static segment
        self.module_name = module_name
        #: unique, monotonically increasing counter for generated labels
        self._label_counter = 0
        #: the assembly collected so far
        self._output: List[str] = []
        #: VM command -> assembly mapping (ready for the visualiser stage)
        self.translations: List[Translation] = []

    # -- public API --------------------------------------------------------

    def write_command(self, command: VMCommand) -> List[str]:
        """Translate one VM command and return its assembly lines."""
        lines = self._translate(command)

        self._output.append(f"// {command.source}")
        self._output.extend(lines)
        self.translations.append(Translation(command, list(lines)))

        return lines

    def write_output(self, asm_path: Path) -> Path:
        """Write the collected assembly to ``asm_path`` and return the path."""
        asm_path = Path(asm_path)
        asm_path.parent.mkdir(parents=True, exist_ok=True)
        asm_path.write_text("\n".join(self._output) + "\n", encoding="utf-8")
        return asm_path

    # -- translation dispatch ----------------------------------------------

    def _translate(self, command: VMCommand) -> List[str]:
        """Route a VM command to the matching translation method."""
        cmd_type = command.cmd_type

        if cmd_type == "push":
            segment, index = command.arg1, int(command.arg2)
            if segment == "constant":
                return self.push_constant(index)
            return self.push_segment(segment, index)

        if cmd_type == "pop":
            return self.pop_segment(command.arg1, int(command.arg2))

        handler = getattr(self, self.ARITHMETIC_HANDLERS[cmd_type], None)
        if handler is None:  # pragma: no cover - defensive
            raise VMTranslationError(f"no translation for '{cmd_type}'")
        return handler()

    # -- stack helpers -----------------------------------------------------

    def _push_value_in_d(self) -> List[str]:
        """``*SP = D`` followed by ``SP++`` (the value must already be in D)."""
        return [
            "@SP",
            "A=M",       # A = SP  -> address of the next free stack slot
            "M=D",       # *SP = D
            "@SP",
            "M=M+1",     # SP++
        ]

    def _pop_value_to_d(self) -> List[str]:
        """Pop the stack top into D (``A`` ends up holding the popped slot)."""
        return [
            "@SP",
            "AM=M-1",    # A = SP-1, SP--   (A -> the popped element)
            "D=M",       # D = *SP
        ]

    def _segment_address_in_r13(self, segment: str, index: int) -> List[str]:
        """Compute the address of ``segment[index]`` into ``R13`` (O(1)).

        Only the segments that are reached through a base pointer register
        (``local``, ``argument``, ``this``, ``that``) need this: their address
        is ``*(LCL|ARG|THIS|THAT) + index``.  The address is built with a
        constant number of instructions, so the size of the generated code
        does not depend on the index.

        ``R13`` is only a scratch cell that lives inside a single VM
        instruction, so it never clashes with the registers used by the
        Chapter 08 instructions.
        """
        base_register = self.SEGMENT_POINTERS[segment]
        return [
            f"@{base_register}",
            "D=M",           # D = base address of the segment
            f"@{index}",
            "D=D+A",         # D = base + index
            f"@{self.SCRATCH}",
            "M=D",           # R13 = base + index
        ]

    def _direct_address(self, segment: str, index: int) -> str:
        """Address of ``segment[index]`` for the directly addressed segments.

        ``temp`` starts at RAM[5], ``pointer`` at RAM[3] (pointer 0 -> THIS,
        pointer 1 -> THAT) and ``static`` uses the symbolic name
        ``<module name>.<index>``.
        """
        if segment == "static":
            return self.static_address(index)
        return str(self.SEGMENT_BASES[segment] + index)

    # -- stack arithmetic --------------------------------------------------

    def push_constant(self, index: int) -> List[str]:
        """``push constant i`` -> pushes the constant ``i``."""
        return [f"@{index}", "D=A"] + self._push_value_in_d()

    def push_segment(self, segment: str, index: int) -> List[str]:
        """``push segment i`` -> pushes ``segment[i]``."""
        if segment in self.SEGMENT_POINTERS:
            return (
                self._segment_address_in_r13(segment, index)
                + [
                    f"@{self.SCRATCH}",
                    "A=M",       # A = address of segment[i]
                    "D=M",       # D = *address
                ]
                + self._push_value_in_d()
            )

        # temp / static / pointer are addressed directly
        return (
            [f"@{self._direct_address(segment, index)}", "D=M"]
            + self._push_value_in_d()
        )

    def pop_segment(self, segment: str, index: int) -> List[str]:
        """``pop segment i`` -> pops the stack top into ``segment[i]``."""
        if segment in self.SEGMENT_POINTERS:
            # The address is computed first (into R13); popping the stack
            # only touches SP, D and A, so R13 survives until the store.
            return (
                self._segment_address_in_r13(segment, index)
                + self._pop_value_to_d()
                + [
                    f"@{self.SCRATCH}",
                    "A=M",       # A = address of segment[i]
                    "M=D",       # *address = popped value
                ]
            )

        # temp / static / pointer are addressed directly
        return (
            self._pop_value_to_d()
            + [f"@{self._direct_address(segment, index)}", "M=D"]
        )

    def add(self) -> List[str]:
        """``add`` -> ``x + y`` (x pushed first, y on top of the stack)."""
        return [
            "@SP",
            "AM=M-1",    # A = SP-1 (y), SP--
            "D=M",       # D = y
            "A=A-1",     # A = SP-2 (x)
            "M=D+M",     # *x = y + x
        ]

    def sub(self) -> List[str]:
        """``sub`` -> ``x - y`` (x pushed first, y on top of the stack)."""
        return [
            "@SP",
            "AM=M-1",    # A = SP-1 (y), SP--
            "D=M",       # D = y
            "A=A-1",     # A = SP-2 (x)
            "M=M-D",     # *x = x - y
        ]

    def neg(self) -> List[str]:
        """``neg`` -> ``-x`` (the top of the stack is replaced in place)."""
        return [
            "@SP",
            "A=M",       # A = SP
            "A=A-1",     # A = SP-1  (the top element)
            "D=M",       # D = x
            "M=-D",      # *x = -x   (SP is left untouched: net 0)
        ]

    def and_(self) -> List[str]:
        """``and`` -> ``x & y`` (bitwise)."""
        return [
            "@SP",
            "AM=M-1",    # A = SP-1 (y), SP--
            "D=M",       # D = y
            "A=A-1",     # A = SP-2 (x)
            "M=D&M",     # *x = y & x
        ]

    def or_(self) -> List[str]:
        """``or`` -> ``x | y`` (bitwise)."""
        return [
            "@SP",
            "AM=M-1",    # A = SP-1 (y), SP--
            "D=M",       # D = y
            "A=A-1",     # A = SP-2 (x)
            "M=D|M",     # *x = y | x
        ]

    def not_(self) -> List[str]:
        """``not`` -> ``!x`` (bitwise, the top of the stack is replaced)."""
        return [
            "@SP",
            "A=M",       # A = SP
            "A=A-1",     # A = SP-1  (the top element)
            "D=M",       # D = x
            "M=!D",      # *x = !x   (SP is left untouched: net 0)
        ]

    # -- comparisons -------------------------------------------------------

    def eq(self) -> List[str]:
        """``eq`` -> ``-1`` (0xFFFF) when x == y, otherwise ``0``."""
        return self._compare("JEQ")

    def gt(self) -> List[str]:
        """``gt`` -> ``-1`` (0xFFFF) when x > y, otherwise ``0``."""
        return self._compare("JGT")

    def lt(self) -> List[str]:
        """``lt`` -> ``-1`` (0xFFFF) when x < y, otherwise ``0``."""
        return self._compare("JLT")

    def _compare(self, jump: str) -> List[str]:
        """Shared implementation of eq/gt/lt.

        ``x`` is pushed first and ``y`` second, so ``y`` is the stack top and
        ``D = x - y`` holds the difference.  The jump is a *signed* comparison
        of D against zero.  Every comparison gets its own pair of unique
        labels (built from a monotonically increasing counter).

        The result (-1 for true, 0 for false) is written back into the slot
        that held ``x``, and the stack pointer stays one below its original
        value: two values were consumed and one is produced.
        """
        self._label_counter += 1
        counter = self._label_counter
        true_label = f"IF_{jump}_{counter}"
        end_label = f"END_{jump}_{counter}"

        return [
            "@SP",
            "AM=M-1",            # A = SP-1 (y), SP--
            "D=M",               # D = y
            "A=A-1",             # A = SP-2 (x)
            "D=M-D",             # D = x - y
            f"@{true_label}",
            f"D;{jump}",         # condition holds -> jump to the true block
            "D=0",               # false
            f"@{end_label}",
            "0;JMP",             # skip the true block
            f"({true_label})",
            "D=-1",              # true  (0xFFFF)
            f"({end_label})",
            "@SP",
            "A=M",               # A = SP
            "A=A-1",             # A = SP-1  (the slot that held x)
            "M=D",               # *x = result
        ]

    # -- static segment ----------------------------------------------------

    def static_address(self, index: int) -> str:
        """``static i`` -> ``<module name>.<i>`` (e.g. ``StaticTest.3``)."""
        return f"{self.module_name}.{index}" if self.module_name else f"{index}"


# ==========================================================================
# SECTION 3 — MAIN
# ==========================================================================


def translate_file(vm_path: Path) -> Path:
    """Translate a single ``.vm`` file and write the sibling ``.asm`` file."""
    vm_path = Path(vm_path)
    parser = Parser(vm_path)
    commands = parser.parse()

    writer = CodeWriter(module_name=vm_path.stem)
    for command in commands:
        writer.write_command(command)

    asm_path = vm_path.with_suffix(".asm")
    writer.write_output(asm_path)
    return asm_path


def main(argv: List[str]) -> int:
    """Entry point: ``python vm_translator.py <file.vm>``."""
    if len(argv) != 2:
        print(f"Usage: {Path(argv[0]).name} <file.vm>", file=sys.stderr)
        print(
            "       Translates a single .vm file into the matching .asm file\n"
            "       (written next to the input).",
            file=sys.stderr,
        )
        return 1

    vm_path = Path(argv[1])
    try:
        asm_path = translate_file(vm_path)
    except VMTranslationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"wrote {asm_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
