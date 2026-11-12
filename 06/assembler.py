#!/usr/bin/env python3
"""Two-pass assembler for the Hack computer (nand2tetris, project 6).

Usage:
    python assembler.py Max.asm
    python assembler.py Max.asm -o build/Max.hack

The implementation is split into four small, single-responsibility classes:

    Parser       - reads/normalizes source lines and exposes instruction fields
    Code         - translates Hack mnemonics into fixed-width binary fields
    SymbolTable  - owns predefined symbols, labels, and allocated variables
    Assembler    - orchestrates pass 1 (symbol scan) and pass 2 (translation)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final, Sequence

# Public instruction kinds, used by Parser and Assembler.
A_COMMAND: Final = "A_COMMAND"
C_COMMAND: Final = "C_COMMAND"
L_COMMAND: Final = "L_COMMAND"

_FIRST_VARIABLE_ADDRESS: Final = 16
_LAST_VARIABLE_ADDRESS: Final = 16_383  # RAM[16384] is SCREEN; stay below it.
_MAX_A_VALUE: Final = 0x7FFF
_WORD_WIDTH: Final = 16

_LABEL_RE: Final = re.compile(r"^\((?P<name>[^()]+)\)$")
_C_INSTRUCTION_RE: Final = re.compile(
    r"^(?:(?P<dest>[ADM]{1,3})=)?(?P<comp>[^=;]+)(?:;(?P<jump>[A-Z]{1,3}))?$"
)
_DECIMAL_RE: Final = re.compile(r"^[0-9]+$")
# Hack symbols start with a letter, '_', '.', '$', or ':' and may then contain
# letters, digits, and those same punctuation characters.
_SYMBOL_RE: Final = re.compile(r"^[A-Za-z_.$:][A-Za-z0-9_.$:]*$")


class AssemblyError(Exception):
    """Raised for any malformed source or unresolvable symbol."""


class Parser:
    """Reads an .asm file once and serves clean instructions sequentially.

    A line's instruction is separated from its ``//`` comment, surrounding and
    internal whitespace is removed, and blank/comment-only lines are discarded.
    The parser therefore exposes a compact, easy-to-encode instruction stream.
    """

    def __init__(self, source: str | Path) -> None:
        self.path = Path(source)
        self._instructions: list[tuple[int, str]] = self._load_instructions()
        self._cursor = 0
        self._current: str | None = None
        self._lineno: int | None = None

    def _load_instructions(self) -> list[tuple[int, str]]:
        try:
            raw_lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise AssemblyError(f"cannot read {self.path}: {exc}") from exc

        instructions: list[tuple[int, str]] = []
        for lineno, line in enumerate(raw_lines, start=1):
            # A comment may follow a label or instruction on the same line,
            # so strip it before deciding whether the line is empty.
            code = line.split("//", 1)[0].strip()
            if not code:
                continue
            # Mnemonics/symbols never contain whitespace; removing it lets
            # forms such as "@ R0" and "D = M" be accepted safely.
            instructions.append((lineno, "".join(code.split())))
        return instructions

    def reset(self) -> None:
        """Rewind for a second pass over the same instruction stream."""
        self._cursor = 0
        self._current = None
        self._lineno = None

    def has_more_commands(self) -> bool:
        return self._cursor < len(self._instructions)

    def advance(self) -> None:
        if not self.has_more_commands():
            raise StopIteration
        self._lineno, self._current = self._instructions[self._cursor]
        self._cursor += 1

    @property
    def current(self) -> str:
        if self._current is None:
            raise AssemblyError("Parser.advance() must be called before reading fields")
        return self._current

    @property
    def location(self) -> str:
        """Human-readable source position used in error messages."""
        line = "?" if self._lineno is None else str(self._lineno)
        return f"{self.path}:{line}"

    @property
    def command_type(self) -> str:
        cmd = self.current
        if cmd.startswith("@"):
            return A_COMMAND
        if _LABEL_RE.fullmatch(cmd):
            return L_COMMAND
        if cmd.startswith("(") or ")" in cmd:
            raise AssemblyError(f"{self.location}: malformed label {cmd!r}")
        return C_COMMAND

    @property
    def symbol(self) -> str:
        """Return the symbol of an A-instruction or the name of a label."""
        kind = self.command_type
        cmd = self.current

        if kind == A_COMMAND:
            symbol = cmd[1:]
            if not symbol:
                raise AssemblyError(f"{self.location}: missing symbol after '@'")
            return symbol

        if kind == L_COMMAND:
            match = _LABEL_RE.fullmatch(cmd)
            # command_type already matched the same pattern, so this is safe.
            assert match is not None
            name = match.group("name")
            if not _SYMBOL_RE.fullmatch(name):
                raise AssemblyError(f"{self.location}: invalid label {name!r}")
            return name

        raise AssemblyError(f"{self.location}: {kind} has no symbol")

    def _c_fields(self) -> re.Match:
        if self.command_type != C_COMMAND:
            raise AssemblyError(f"{self.location}: {self.command_type} has no C fields")
        match = _C_INSTRUCTION_RE.fullmatch(self.current)
        if match is None:
            raise AssemblyError(
                f"{self.location}: malformed C-instruction {self.current!r}"
            )
        return match

    @property
    def dest(self) -> str | None:
        return self._c_fields().group("dest")

    @property
    def comp(self) -> str:
        return self._c_fields().group("comp")

    @property
    def jump(self) -> str | None:
        return self._c_fields().group("jump")


class Code:
    """Static mnemonic-to-binary translation tables for C-instructions."""

    # 7-bit comp field. The 'a' bit is the leading bit: 0 selects A, 1 selects M.
    # The four subtraction forms are supported by the Hack ALU and are used by
    # official project programs (e.g. "D=D-M" in Max.asm, "M-D" in Pong.asm).
    _COMP_TABLE: Final[dict[str, str]] = {
        "0": "0101010",
        "1": "0111111",
        "-1": "0111010",
        "D": "0001100",
        "A": "0110000",
        "!D": "0001101",
        "!A": "0110001",
        "-D": "0001111",
        "-A": "0110011",
        "D+1": "0011111",
        "A+1": "0110111",
        "D-1": "0001110",
        "A-1": "0110010",
        "D+A": "0000010",
        "D-A": "0010011",
        "A-D": "0000111",
        "D&A": "0000000",
        "D|A": "0010101",
        "M": "1110000",
        "!M": "1110001",
        "-M": "1110011",
        "M+1": "1110111",
        "M-1": "1110010",
        "D+M": "1000010",
        "D-M": "1010011",
        "M-D": "1000111",
        "D&M": "1000000",
        "D|M": "1010101",
    }

    # 3-bit dest field; bits are A, D, M from left to right.
    _DEST_TABLE: Final[dict[str, str]] = {
        "M": "001",
        "D": "010",
        "MD": "011",
        "A": "100",
        "AM": "101",
        "AD": "110",
        "AMD": "111",
    }

    # 3-bit jump field.
    _JUMP_TABLE: Final[dict[str, str]] = {
        "JGT": "001",
        "JEQ": "010",
        "JGE": "011",
        "JLT": "100",
        "JNE": "101",
        "JLE": "110",
        "JMP": "111",
    }

    @classmethod
    def comp(cls, mnemonic: str) -> str:
        try:
            return cls._COMP_TABLE[mnemonic]
        except KeyError:
            raise AssemblyError(f"unknown comp mnemonic {mnemonic!r}") from None

    @classmethod
    def dest(cls, mnemonic: str | None) -> str:
        if mnemonic is None:
            return "000"
        try:
            return cls._DEST_TABLE[mnemonic]
        except KeyError:
            raise AssemblyError(f"unknown dest mnemonic {mnemonic!r}") from None

    @classmethod
    def jump(cls, mnemonic: str | None) -> str:
        if mnemonic is None:
            return "000"
        try:
            return cls._JUMP_TABLE[mnemonic]
        except KeyError:
            raise AssemblyError(f"unknown jump mnemonic {mnemonic!r}") from None


class SymbolTable:
    """Maps symbols to addresses, including the Hack predefined symbols.

    Labels are added during pass 1. Undefined symbols encountered in
    A-instructions during pass 2 are allocated consecutive RAM addresses
    starting at 16, as specified by the Hack assembly language.
    """

    PREDEFINED: Final[dict[str, int]] = {
        "SP": 0,
        "LCL": 1,
        "ARG": 2,
        "THIS": 3,
        "THAT": 4,
        "SCREEN": 16_384,
        "KBD": 24_576,
        **{f"R{i}": i for i in range(16)},
    }

    def __init__(self) -> None:
        self._symbols: dict[str, int] = dict(self.PREDEFINED)
        self._next_variable_address = _FIRST_VARIABLE_ADDRESS

    def add_entry(self, symbol: str, address: int) -> None:
        if not 0 <= address <= _MAX_A_VALUE:
            raise AssemblyError(
                f"address {address} for symbol {symbol!r} does not fit in 15 bits"
            )
        self._symbols[symbol] = address

    def contains(self, symbol: str) -> bool:
        return symbol in self._symbols

    def get_address(self, symbol: str) -> int:
        try:
            return self._symbols[symbol]
        except KeyError:
            raise AssemblyError(f"undefined symbol {symbol!r}") from None

    def add_variable(self, symbol: str) -> int:
        """Bind a new variable to the next free RAM word and return its address."""
        if self._next_variable_address > _LAST_VARIABLE_ADDRESS:
            raise AssemblyError("variable space exhausted (RAM[16..16383] is full)")

        address = self._next_variable_address
        self._next_variable_address += 1
        self.add_entry(symbol, address)
        return address


class Assembler:
    """Executes the two-pass assembly algorithm over a Parser instance."""

    def __init__(self, source: str | Path) -> None:
        self._parser = Parser(source)
        self._symbols = SymbolTable()

    def assemble(self) -> list[str]:
        """Return one 16-character binary word per executable instruction."""
        self._first_pass()
        return self._second_pass()

    def _first_pass(self) -> None:
        """Record label declarations against their ROM instruction addresses."""
        self._parser.reset()
        rom_address = 0

        while self._parser.has_more_commands():
            self._parser.advance()
            if self._parser.command_type == L_COMMAND:
                name = self._parser.symbol
                if self._symbols.contains(name):
                    raise AssemblyError(
                        f"{self._parser.location}: duplicate symbol {name!r}"
                    )
                # The label denotes the address of the *next* real instruction.
                self._symbols.add_entry(name, rom_address)
            else:
                rom_address += 1

    def _second_pass(self) -> list[str]:
        """Translate every instruction, allocating variables on first use."""
        self._parser.reset()
        words: list[str] = []

        while self._parser.has_more_commands():
            self._parser.advance()
            kind = self._parser.command_type

            if kind == A_COMMAND:
                words.append(self._encode_a(self._parser.symbol))
            elif kind == C_COMMAND:
                words.append(self._encode_c())
            # L_COMMAND declarations produce no machine code.

        return words

    def _encode_a(self, symbol: str) -> str:
        """Encode '@value'/'@symbol' as 0 followed by a 15-bit address."""
        if _DECIMAL_RE.fullmatch(symbol):
            value = int(symbol, 10)
            if value > _MAX_A_VALUE:
                raise AssemblyError(
                    f"{self._parser.location}: constant {value} exceeds 32767"
                )
        else:
            if not _SYMBOL_RE.fullmatch(symbol):
                raise AssemblyError(
                    f"{self._parser.location}: invalid symbol {symbol!r}"
                )
            # Forward references were resolved in pass 1; anything still
            # unknown is a variable and gets the next available RAM address.
            if not self._symbols.contains(symbol):
                self._symbols.add_variable(symbol)
            value = self._symbols.get_address(symbol)

        return f"{value:0{_WORD_WIDTH}b}"

    def _encode_c(self) -> str:
        """Encode a C-instruction as 111 + comp(7) + dest(3) + jump(3)."""
        # Parser errors already carry file/line context; only wrap table errors.
        comp, dest, jump = self._parser.comp, self._parser.dest, self._parser.jump
        try:
            fields = Code.comp(comp) + Code.dest(dest) + Code.jump(jump)
        except AssemblyError as exc:
            raise AssemblyError(f"{self._parser.location}: {exc}") from exc
        return "111" + fields


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate a Hack .asm program into 16-bit .hack machine code."
    )
    parser.add_argument("source", metavar="FILE.asm", type=Path, help="input assembly file")
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE.hack",
        type=Path,
        help="output path (default: input path with a .hack suffix)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source: Path = args.source
    destination: Path = args.output or source.with_suffix(".hack")

    try:
        words = Assembler(source).assemble()
    except AssemblyError as exc:
        print(f"assembler.py: error: {exc}", file=sys.stderr)
        return 1

    # Write bytes so the line separator is exactly '\n' on every platform.
    text = "\n".join(words) + ("\n" if words else "")
    try:
        destination.write_bytes(text.encode("ascii"))
    except OSError as exc:
        print(f"assembler.py: error: cannot write {destination}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
