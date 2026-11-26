#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nand2Tetris — Chapter 11 Jack Compiler (Jack -> VM)
====================================================

A from-scratch implementation written in pure Python 3 (standard library
only: ``sys``, ``pathlib``, ``dataclasses``, ``typing``).  It translates a
Jack source file directly into Hack VM code — the XML intermediate
representation of Chapter 10 is deliberately *not* produced.

Usage
-----
    python 11/jack_compiler.py <path/to/file.jack>
    python 11/jack_compiler.py <path/to/folder>

* a single ``.jack`` file -> ``<same folder>/<name>.vm``
* a folder               -> one ``<name>.vm`` per ``.jack`` file inside it

Module layout
-------------
1. ``JackTokenizer``    – removes the comments and turns the remaining text
                         into a list of tokens.  Two tokens of lookahead
                         (``peek(0)`` / ``peek(1)``) are available, which is
                         what ``compileTerm`` needs in order to tell a
                         ``varName`` from a ``varName[expr]`` and from a
                         ``subroutineCall``.
2. ``SymbolTable``      – the two-level symbol table of the Jack language:
                         class scope (``static`` / ``field``) and subroutine
                         scope (``arg`` / ``var``).
3. ``VMWriter``         – emits the VM commands.  Besides the usual
                         ``write_*`` methods it can ``mark()`` the current
                         output position and ``insert_at()`` a block of
                         commands there, which is how the ``function`` header
                         and the constructor/method prologue are placed in
                         front of a subroutine body whose size is only known
                         after the body has been compiled.
4. ``CompilationEngine` – walks the token stream and drives the other three
                         classes.

Language notes (all of them verified against the reference compiler that
ships with the Nand2Tetris software suite)
--------------------------------------------
* **Operator precedence.**  Jack has *no* operator precedence: every
  expression is evaluated strictly from left to right, so ``1 + 2 * 3`` means
  ``(1 + 2) * 3``.  This implementation follows that rule.
* **Fields are invisible inside a ``function``.**  A function has no
  ``this``, so it may only reach the class' ``static`` variables.  The
  reference compiler rejects such an access; so does this one.
* **Unqualified calls.**  ``f(...)`` refers to a subroutine of the current
  class.  When ``f`` is a method the receiver (``this``) is pushed and the
  argument count is increased by one; when ``f`` is a function or a
  constructor it is called like a static routine.
* **Qualified calls.**  ``p.f(...)`` first looks ``p`` up in the symbol
  table.  If ``p`` is a variable the call is a method call on that object
  (``push p`` + one extra argument); otherwise ``p`` is a class name and the
  call is a static one.
* **The order of the arguments.**  The receiver of a method call is pushed
  *before* the arguments are evaluated: the VM pops the arguments from the
  stack in the order in which they were pushed, so the receiver has to be the
  first value on the stack.
* **Array assignments.**  The address of the array element stays on the
  stack while the value is computed; it is moved into THAT (``pop pointer
  1``) only afterwards.  Computing the address first and storing it in THAT
  would break as soon as the value accesses an array as well.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# ==========================================================================
# SECTION 0 — CONSTANTS, ERRORS AND SMALL HELPERS
# ==========================================================================

#: The reserved words of the Jack language.
KEYWORDS = frozenset(
    {
        "class",
        "constructor",
        "function",
        "method",
        "field",
        "static",
        "var",
        "int",
        "char",
        "boolean",
        "void",
        "true",
        "false",
        "null",
        "this",
        "let",
        "do",
        "if",
        "else",
        "while",
        "return",
    }
)

#: The single character symbols of the Jack language.
SYMBOLS = frozenset("{}()[].,;+-*/=<>&|~")

#: The VM segment every Jack variable kind is stored in.
VM_SEGMENTS = {
    "static": "static",
    "field": "this",
    "arg": "argument",
    "var": "local",
}

#: The three kinds of subroutine a class may declare.
SUBROUTINE_KINDS = frozenset({"constructor", "function", "method"})

#: The binary operators of Jack and the VM command each one compiles to.
BINARY_OPERATORS = {
    "+": "add",
    "-": "sub",
    "*": "call Math.multiply 2",
    "/": "call Math.divide 2",
    ">": "gt",
    "<": "lt",
    "=": "eq",
    "&": "and",
    "|": "or",
}


class JackCompilationError(Exception):
    """Raised when a ``.jack`` file cannot be compiled."""


# ==========================================================================
# SECTION 1 — TOKENIZER
# ==========================================================================


@dataclass(frozen=True)
class Token:
    """One token of a Jack program.

    ``kind``   ``keyword`` | ``symbol`` | ``integer`` | ``string`` |
               ``identifier``
    ``value``  the text of the token (for a string constant the text
               *between* the quotes)
    ``line``   the 1-based line the token starts on (used in error messages)
    """

    kind: str
    value: str
    line: int


def strip_comments(text: str) -> str:
    """Remove the comments of a Jack program.

    Three kinds of comments are removed:

    * ``// ...``      a single line comment,
    * ``/* ... */``   a multi line comment,
    * ``/** ... */``  a documentation comment (a special case of the above).

    A string constant is copied verbatim, so a ``//`` or a ``/*`` inside a
    string literal is *not* mistaken for the beginning of a comment.  Newlines
    inside a multi line comment are kept so that the line numbers of the
    error messages stay correct.
    """
    out: List[str] = []
    i = 0
    n = len(text)

    while i < n:
        char = text[i]

        if char == '"':
            # a string constant: copy it verbatim up to and including the
            # closing quote
            start = i
            i += 1
            while i < n and text[i] != '"':
                i += 1
            i = min(i + 1, n)
            out.append(text[start:i])

        elif char == "/" and i + 1 < n and text[i + 1] == "/":
            # a single line comment: skip up to (but not including) the newline
            while i < n and text[i] != "\n":
                i += 1

        elif char == "/" and i + 1 < n and text[i + 1] == "*":
            # a multi line (or documentation) comment
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i = min(i + 2, n)

        else:
            out.append(char)
            i += 1

    return "".join(out)


class JackTokenizer:
    """Splits a Jack program into a list of :class:`Token` objects.

    The tokenizer keeps the whole token list in memory and moves an index
    through it, which makes the two token lookahead of ``compileTerm``
    trivial: ``peek(0)`` is the token that ``advance()`` will return next
    and ``peek(1)`` is the one behind it.
    """

    def __init__(self, text: str) -> None:
        self.tokens: List[Token] = self._tokenize(strip_comments(text))
        self.pos = 0

    # -- public API --------------------------------------------------------

    def advance(self) -> Token:
        """Return the current token and move past it."""
        if self.pos >= len(self.tokens):
            raise JackCompilationError("unexpected end of file")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def back_up(self) -> None:
        """Move one token back."""
        if self.pos == 0:
            raise JackCompilationError("cannot back up before the first token")
        self.pos -= 1

    def peek(self, offset: int = 0) -> Optional[Token]:
        """Return the token ``offset`` positions ahead of the cursor.

        ``peek(0)`` is the token ``advance()`` would return next, ``peek(1)``
        the one behind it.  ``None`` is returned past the end of the file.
        """
        index = self.pos + offset
        if 0 <= index < len(self.tokens):
            return self.tokens[index]
        return None

    def has_more(self) -> bool:
        """``True`` while there are tokens left."""
        return self.pos < len(self.tokens)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> List[Token]:
        """Scan the comment-free text and build the token list."""
        tokens: List[Token] = []
        i = 0
        n = len(text)
        line = 1

        while i < n:
            char = text[i]

            if char == "\n":
                line += 1
                i += 1
                continue

            if char.isspace():
                i += 1
                continue

            if char == '"':
                end = text.find('"', i + 1)
                if end < 0:
                    raise JackCompilationError(
                        f"line {line}: unterminated string constant"
                    )
                tokens.append(Token("string", text[i + 1 : end], line))
                line += text.count("\n", i, end)
                i = end + 1
                continue

            if char.isdigit():
                end = i
                while end < n and text[end].isdigit():
                    end += 1
                tokens.append(Token("integer", text[i:end], line))
                i = end
                continue

            if char.isalpha() or char == "_":
                end = i
                while end < n and (text[end].isalnum() or text[end] == "_"):
                    end += 1
                word = text[i:end]
                kind = "keyword" if word in KEYWORDS else "identifier"
                tokens.append(Token(kind, word, line))
                i = end
                continue

            if char in SYMBOLS:
                tokens.append(Token("symbol", char, line))
                i += 1
                continue

            raise JackCompilationError(
                f"line {line}: unexpected character {char!r}"
            )

        return tokens


# ==========================================================================
# SECTION 2 — SYMBOL TABLE
# ==========================================================================


@dataclass(frozen=True)
class Symbol:
    """One entry of the symbol table."""

    name: str
    type: str
    kind: str
    index: int


class SymbolTable:
    """The two-level symbol table of the Jack language.

    * the **class scope** holds the ``static`` and the ``field`` variables,
    * the **subroutine scope** holds the ``arg`` and the ``var`` variables.

    A lookup first searches the subroutine scope and only then the class
    scope, so a local variable shadows a field of the same name.
    """

    def __init__(self) -> None:
        self._class_scope: Dict[str, Symbol] = {}
        self._subroutine_scope: Dict[str, Symbol] = {}
        self._counters: Dict[str, int] = {"static": 0, "field": 0, "arg": 0, "var": 0}

    # -- scopes ------------------------------------------------------------

    def start_subroutine(self) -> None:
        """Open a new subroutine scope (the counters restart)."""
        self._subroutine_scope.clear()
        self._counters["arg"] = 0
        self._counters["var"] = 0

    def end_subroutine(self) -> None:
        """Close the current subroutine scope."""
        self._subroutine_scope.clear()

    # -- declarations ------------------------------------------------------

    def define(self, name: str, var_type: str, kind: str) -> Symbol:
        """Declare a class level variable (``static`` or ``field``)."""
        index = self._counters[kind]
        self._counters[kind] = index + 1
        symbol = Symbol(name, var_type, kind, index)
        self._class_scope[name] = symbol
        return symbol

    def declare(self, name: str, var_type: str, kind: str) -> Symbol:
        """Declare a subroutine level variable (``arg`` or ``var``)."""
        index = self._counters[kind]
        self._counters[kind] = index + 1
        symbol = Symbol(name, var_type, kind, index)
        self._subroutine_scope[name] = symbol
        return symbol

    # -- queries -----------------------------------------------------------

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look a name up: subroutine scope first, class scope second."""
        symbol = self._subroutine_scope.get(name)
        if symbol is not None:
            return symbol
        return self._class_scope.get(name)

    def var_count(self, kind: str) -> int:
        """How many variables of ``kind`` the current scope holds."""
        return self._counters[kind]

    def field_count(self) -> int:
        """How many ``field`` variables the class declares."""
        return self._counters["field"]

    def static_count(self) -> int:
        """How many ``static`` variables the class declares."""
        return self._counters["static"]


# ==========================================================================
# SECTION 3 — VM WRITER
# ==========================================================================


class VMWriter:
    """Collects the VM commands of one class.

    ``mark()`` / ``insert_at()`` exist because the ``function`` header of a
    subroutine has to be the very first command of that subroutine, while the
    number of locals it announces is only known after the body has been
    compiled.  The body is therefore compiled first and the header is
    inserted in front of it afterwards.
    """

    def __init__(self) -> None:
        self.commands: List[str] = []

    # -- public API --------------------------------------------------------

    def write_push(self, segment: str, index: int) -> None:
        """``push segment index``"""
        self.commands.append(f"push {segment} {index}")

    def write_pop(self, segment: str, index: int) -> None:
        """``pop segment index``"""
        self.commands.append(f"pop {segment} {index}")

    def write_arithmetic(self, command: str) -> None:
        """An arithmetic command (``add``, ``neg``, ``call Math.multiply 2``...)."""
        self.commands.append(command)

    def write_label(self, label: str) -> None:
        """``label label``"""
        self.commands.append(f"label {label}")

    def write_goto(self, label: str) -> None:
        """``goto label``"""
        self.commands.append(f"goto {label}")

    def write_if(self, label: str) -> None:
        """``if-goto label``"""
        self.commands.append(f"if-goto {label}")

    def write_call(self, name: str, n_args: int) -> None:
        """``call name nArgs``"""
        self.commands.append(f"call {name} {n_args}")

    def write_function(self, name: str, n_locals: int) -> None:
        """``function name nLocals``"""
        self.commands.append(f"function {name} {n_locals}")

    def write_return(self) -> None:
        """``return``"""
        self.commands.append("return")

    # -- output helpers ----------------------------------------------------

    def mark(self) -> int:
        """Return the position the next command will be written to."""
        return len(self.commands)

    def insert_at(self, index: int, commands: Sequence[str]) -> None:
        """Insert ``commands`` at ``index``."""
        self.commands[index:index] = list(commands)

    def output(self) -> str:
        """The whole program as text."""
        return "\n".join(self.commands) + "\n"


# ==========================================================================
# SECTION 4 — COMPILATION ENGINE
# ==========================================================================


class CompilationEngine:
    """Turns a token stream into VM commands.

    The engine is a plain recursive descent parser.  Every ``compile_*``
    method consumes the tokens of one grammar rule and writes the matching
    VM commands into the :class:`VMWriter`.
    """

    def __init__(self, tokenizer: JackTokenizer, writer: VMWriter) -> None:
        self.tk = tokenizer
        self.writer = writer

        #: name of the class that is being compiled
        self.class_name: str = ""
        #: ``constructor`` / ``function`` / ``method`` of the current subroutine
        self.subroutine_kind: str = ""
        #: name of the current subroutine
        self.subroutine_name: str = ""
        #: name -> kind of every subroutine of the current class
        self.class_subroutines: Dict[str, str] = {}

        self.symbols = SymbolTable()

        #: counters for the generated labels (they restart for every
        #: subroutine, so that the labels of two subroutines never collide)
        self._if_counter = 0
        self._while_counter = 0

    # ------------------------------------------------------------------
    # class
    # ------------------------------------------------------------------

    def compile_class(self) -> None:
        """Compile a whole ``class`` declaration."""
        self._expect_keyword("class")
        self.class_name = self._expect_identifier("the class name")
        self._expect_symbol("{")

        # The kinds of the subroutines are collected up front: a subroutine
        # may call another one that is declared further down in the file.
        self.class_subroutines = self._scan_class_subroutines(self.tk.pos)

        while self._peek_keyword("static", "field"):
            self._compile_class_var()

        while self._peek_keyword(*SUBROUTINE_KINDS):
            self._compile_subroutine()

        self._expect_symbol("}")

    def _compile_class_var(self) -> None:
        """Compile a ``static``/``field`` declaration."""
        kind = self.tk.advance().value
        var_type = self.tk.advance().value

        while True:
            name = self._expect_identifier("a variable name")
            self.symbols.define(name, var_type, kind)
            if self._peek_symbol(","):
                self.tk.advance()
                continue
            break

        self._expect_symbol(";")

    # ------------------------------------------------------------------
    # subroutine
    # ------------------------------------------------------------------

    def _compile_subroutine(self) -> None:
        """Compile a ``constructor`` / ``function`` / ``method``."""
        kind = self.tk.advance().value
        self.tk.advance()  # the return type
        name = self._expect_identifier("a subroutine name")

        self.subroutine_kind = kind
        self.subroutine_name = name
        self._if_counter = 0
        self._while_counter = 0

        self.symbols.start_subroutine()
        self._expect_symbol("(")

        # A method receives the object as its first argument: "this" has to
        # occupy argument 0 *before* the parameter list is parsed, otherwise
        # the first parameter would be argument 0 as well.
        if kind == "method":
            self.symbols.declare("this", self.class_name, "arg")

        if not self._peek_symbol(")"):
            while True:
                param_type = self.tk.advance().value
                param_name = self._expect_identifier("a parameter name")
                self.symbols.declare(param_name, param_type, "arg")
                if self._peek_symbol(","):
                    self.tk.advance()
                    continue
                break

        self._expect_symbol(")")

        # The body is compiled first; the "function" header (which announces
        # the number of locals) is inserted in front of it afterwards.
        self._expect_symbol("{")
        self._compile_var_declarations()
        mark = self.writer.mark()
        self._compile_statement_list()
        self._expect_symbol("}")

        header = [f"function {self.class_name}.{name} {self.symbols.var_count('var')}"]
        if kind == "constructor":
            # allocate the object and make it "this"
            header += [
                f"push constant {self.symbols.field_count()}",
                "call Memory.alloc 1",
                "pop pointer 0",
            ]
        elif kind == "method":
            # the object arrives as argument 0
            header += ["push argument 0", "pop pointer 0"]

        self.writer.insert_at(mark, header)
        self.symbols.end_subroutine()

    # ------------------------------------------------------------------
    # statements
    # ------------------------------------------------------------------

    def _compile_statements(self) -> None:
        """Compile a ``{ statement { statement } }`` block."""
        self._expect_symbol("{")
        self._compile_statement_list()
        self._expect_symbol("}")

    def _compile_statement_list(self) -> None:
        """Compile a list of statements (without the surrounding braces)."""
        while not self._peek_symbol("}"):
            if not self.tk.has_more():
                raise JackCompilationError("unexpected end of file inside a block")
            self._compile_statement()

    def _compile_var_declarations(self) -> None:
        """Compile the ``var`` declarations at the head of a subroutine body."""
        while self._peek_keyword("var"):
            self.tk.advance()
            var_type = self.tk.advance().value
            while True:
                name = self._expect_identifier("a variable name")
                self.symbols.declare(name, var_type, "var")
                if self._peek_symbol(","):
                    self.tk.advance()
                    continue
                break
            self._expect_symbol(";")

    def _compile_statement(self) -> None:
        """Compile a single statement."""
        token = self.tk.advance()

        if token.kind == "keyword":
            if token.value == "let":
                self._compile_let()
            elif token.value == "do":
                self._compile_do()
            elif token.value == "if":
                self._compile_if()
            elif token.value == "while":
                self._compile_while()
            elif token.value == "return":
                self._compile_return()
            else:
                raise JackCompilationError(
                    f"line {token.line}: unexpected keyword '{token.value}'"
                )
        elif token.kind == "symbol" and token.value == "{":
            # a bare block of statements
            self.tk.back_up()
            self._compile_statements()
        else:
            raise JackCompilationError(
                f"line {token.line}: unexpected token '{token.value}'"
            )

    def _compile_let(self) -> None:
        """Compile ``let varName = expr;`` and ``let varName[expr] = expr;``."""
        name = self._expect_identifier("a variable name")

        if self._peek_symbol("["):
            # An array assignment.  The address of the array element has to
            # survive the evaluation of the right hand side, so it is left on
            # the stack while the value is computed:
            #
            #     <index> <base> add          -> the address
            #     <value>                     -> the value
            #     pop temp 0                  -> save the value
            #     pop pointer 1               -> THAT = the address
            #     push temp 0                 -> restore the value
            #     pop that 0                  -> *THAT = the value
            #
            # Computing the address *after* the value would be wrong: the
            # value may access an array as well and would overwrite THAT.
            self._compile_array_address(name)
            self._expect_symbol("=")
            self._compile_expression()
            self.writer.write_pop("temp", 0)
            self.writer.write_pop("pointer", 1)
            self.writer.write_push("temp", 0)
            self.writer.write_pop("that", 0)
        else:
            self._expect_symbol("=")
            self._compile_expression()
            symbol = self._lookup_variable(name)
            self.writer.write_pop(VM_SEGMENTS[symbol.kind], symbol.index)

        self._expect_symbol(";")

    def _compile_do(self) -> None:
        """Compile ``do subroutineCall;``."""
        name = self._expect_identifier("a subroutine name")
        if self._peek_symbol("("):
            self._compile_subroutine_call(name, None)
        elif self._peek_symbol("."):
            self.tk.advance()
            sub_name = self._expect_identifier("a subroutine name")
            self._compile_subroutine_call(sub_name, name)
        else:
            raise JackCompilationError(
                f"line {self._current_line()}: expected a subroutine call"
            )

        # the (possibly void) return value of the call is discarded
        self.writer.write_pop("temp", 0)
        self._expect_symbol(";")

    def _compile_if(self) -> None:
        """Compile ``if (expr) { ... } [else { ... }]``."""
        counter = self._if_counter
        self._if_counter += 1
        true_label = f"IF_TRUE{counter}"
        false_label = f"IF_FALSE{counter}"
        end_label = f"IF_END{counter}"

        self._expect_symbol("(")
        self._compile_expression()
        self._expect_symbol(")")

        self.writer.write_if(true_label)
        self.writer.write_goto(false_label)
        self.writer.write_label(true_label)
        self._compile_statements()

        if self._peek_keyword("else"):
            self.tk.advance()
            self.writer.write_goto(end_label)
            self.writer.write_label(false_label)
            self._compile_statements()
            self.writer.write_label(end_label)
        else:
            self.writer.write_label(false_label)

    def _compile_while(self) -> None:
        """Compile ``while (expr) { ... }``."""
        counter = self._while_counter
        self._while_counter += 1
        exp_label = f"WHILE_EXP{counter}"
        end_label = f"WHILE_END{counter}"

        self._expect_symbol("(")
        self.writer.write_label(exp_label)
        self._compile_expression()
        self._expect_symbol(")")
        # the loop is left when the condition is false
        self.writer.write_arithmetic("not")
        self.writer.write_if(end_label)
        self._compile_statements()
        self.writer.write_goto(exp_label)
        self.writer.write_label(end_label)

    def _compile_return(self) -> None:
        """Compile ``return;`` and ``return expr;``."""
        if self._peek_symbol(";"):
            self.tk.advance()
            # a void subroutine still has to leave a value on the stack
            self.writer.write_push("constant", 0)
        else:
            self._compile_expression()
            self._expect_symbol(";")
        self.writer.write_return()

    # ------------------------------------------------------------------
    # expressions
    # ------------------------------------------------------------------

    def _compile_expression(self) -> None:
        """Compile ``expression``.

        The binary operators of Jack are ``+ - * / > < = & |``.  Jack has no
        operator precedence: the operators are evaluated strictly from left
        to right.
        """
        self._compile_term()
        while True:
            token = self.tk.peek(0)
            if token is None or token.kind != "symbol":
                break
            operator = token.value
            if operator not in BINARY_OPERATORS:
                break
            self.tk.advance()
            self._compile_term()
            self.writer.write_arithmetic(BINARY_OPERATORS[operator])

    def _compile_term(self) -> None:
        """Compile ``term``."""
        token = self.tk.advance()

        if token.kind == "integer":
            value = int(token.value)
            if value < 0:
                self.writer.write_push("constant", -value)
                self.writer.write_arithmetic("neg")
            else:
                self.writer.write_push("constant", value)

        elif token.kind == "string":
            self._compile_string_constant(token.value)

        elif token.kind == "keyword":
            if token.value == "true":
                self.writer.write_push("constant", 0)
                self.writer.write_arithmetic("not")
            elif token.value in ("false", "null"):
                self.writer.write_push("constant", 0)
            elif token.value == "this":
                self.writer.write_push("pointer", 0)
            else:
                raise JackCompilationError(
                    f"line {token.line}: unexpected keyword '{token.value}'"
                )

        elif token.kind == "symbol":
            if token.value == "(":
                self._compile_expression()
                self._expect_symbol(")")
            elif token.value == "-":
                self._compile_term()
                self.writer.write_arithmetic("neg")
            elif token.value == "~":
                self._compile_term()
                self.writer.write_arithmetic("not")
            else:
                raise JackCompilationError(
                    f"line {token.line}: unexpected symbol '{token.value}'"
                )

        elif token.kind == "identifier":
            self._compile_identifier_term(token)

        else:  # pragma: no cover - defensive
            raise JackCompilationError(
                f"line {token.line}: unexpected token '{token.value}'"
            )

    def _compile_identifier_term(self, token: Token) -> None:
        """Compile a term that starts with an identifier.

        The two tokens of lookahead decide what the identifier is:

        * ``name [``    an array access,
        * ``name (``    a call of a subroutine of the current class,
        * ``name .``    a call of a subroutine of another class or object,
        * ``name``      a plain variable.
        """
        following = self.tk.peek(0)

        if following is not None and following.kind == "symbol":
            if following.value == "[":
                # varName[expression]
                self._compile_array_address(token.value)
                self.writer.write_pop("pointer", 1)
                self.writer.write_push("that", 0)
                return

            if following.value == "(":
                # subroutineCall
                self._compile_subroutine_call(token.value, None)
                return

            if following.value == ".":
                # varName.subroutineName(expressionList)
                self.tk.advance()  # the '.'
                sub_name = self._expect_identifier("a subroutine name")
                self._compile_subroutine_call(sub_name, token.value)
                return

        # a plain variable
        self._push_variable(token.value)

    def _compile_array_address(self, name: str) -> None:
        """Push the address of ``name[expression]`` onto the stack.

        The address is built as ``<index> <base> add``; it is *not* stored in
        THAT yet, because the caller may still evaluate an expression that
        uses THAT itself.
        """
        self._expect_symbol("[")
        self._compile_expression()
        self._expect_symbol("]")
        self._push_variable(name)
        self.writer.write_arithmetic("add")

    def _compile_string_constant(self, value: str) -> None:
        """Compile a string constant.

        ``push constant length`` + ``call String.new 1`` creates the string
        and every character is appended with ``call String.appendChar 2``.
        """
        self.writer.write_push("constant", len(value))
        self.writer.write_arithmetic("call String.new 1")
        for char in value:
            self.writer.write_push("constant", ord(char))
            self.writer.write_arithmetic("call String.appendChar 2")

    def _compile_expression_list(self) -> int:
        """Compile ``expression {, expression}`` and return the count."""
        count = 0
        if self._peek_symbol(")"):
            return count

        while True:
            self._compile_expression()
            count += 1
            if self._peek_symbol(","):
                self.tk.advance()
                continue
            break
        return count

    # ------------------------------------------------------------------
    # subroutine calls
    # ------------------------------------------------------------------

    def _compile_subroutine_call(self, name: str, prefix: Optional[str]) -> None:
        """Compile a subroutine call.

        ``prefix`` is ``None`` for an unqualified call (``f(...)``) and the
        name of the object or of the class for a qualified one
        (``p.f(...)``).

        The receiver of a method call is pushed **before** the arguments are
        evaluated: the VM pops the arguments from the stack in the order in
        which they were pushed, so the receiver has to be the first one.
        """
        receiver_segment: Optional[Tuple[str, int]] = None
        receiver_type: Optional[str] = None

        if prefix is None:
            # f(...): a subroutine of the current class
            kind = self.class_subroutines.get(name)
            if kind is None:
                # The subroutine is not declared in this class: assume it is
                # a method, which is the only case in which the receiver can
                # be passed on.
                is_method = self.subroutine_kind in ("method", "constructor")
            else:
                is_method = kind == "method"
            if is_method:
                # the receiver is the current object
                receiver_segment = ("pointer", 0)
                receiver_type = self.class_name
        else:
            # p.f(...): a method of an object or a static routine of a class
            symbol = self.symbols.lookup(prefix)
            if symbol is not None and not (
                symbol.kind == "field" and self.subroutine_kind == "function"
            ):
                # a function has no "this" and cannot reach a field
                receiver_segment = (VM_SEGMENTS[symbol.kind], symbol.index)
                receiver_type = symbol.type

        if receiver_segment is not None:
            self.writer.write_push(receiver_segment[0], receiver_segment[1])

        self._expect_symbol("(")
        n_args = self._compile_expression_list()
        self._expect_symbol(")")

        if receiver_type is not None:
            # a method: the receiver is the first argument
            self.writer.write_call(f"{receiver_type}.{name}", n_args + 1)
        elif prefix is not None:
            # a static function or a constructor of another class
            self.writer.write_call(f"{prefix}.{name}", n_args)
        else:
            # a function of the current class
            self.writer.write_call(f"{self.class_name}.{name}", n_args)

    # ------------------------------------------------------------------
    # variables
    # ------------------------------------------------------------------

    def _lookup_variable(self, name: str) -> Symbol:
        """Look a variable up and make sure it may be used here."""
        symbol = self.symbols.lookup(name)
        if symbol is None:
            raise JackCompilationError(
                f"line {self._current_line()}: '{name}' is not defined as a "
                f"field, parameter or local or static variable"
            )
        if symbol.kind == "field" and self.subroutine_kind == "function":
            raise JackCompilationError(
                f"line {self._current_line()}: the field '{name}' cannot be "
                f"used inside a function"
            )
        return symbol

    def _push_variable(self, name: str) -> None:
        """Push the value of a variable onto the stack."""
        self._push_symbol(self._lookup_variable(name))

    def _push_symbol(self, symbol: Symbol) -> None:
        """Push the value of an already resolved symbol onto the stack."""
        self.writer.write_push(VM_SEGMENTS[symbol.kind], symbol.index)

    # ------------------------------------------------------------------
    # the class scanner
    # ------------------------------------------------------------------

    def _scan_class_subroutines(self, start: int) -> Dict[str, str]:
        """Collect ``name -> kind`` for every subroutine of the class.

        The scan runs over the token list, starting at ``start`` (the first
        token behind the opening brace of the class) and stops at the closing
        brace of the class body.  It is needed because a subroutine may call
        another one that is declared further down in the file.
        """
        kinds: Dict[str, str] = {}
        tokens = self.tk.tokens
        index = start
        total = len(tokens)

        while index < total:
            token = tokens[index]
            if token.kind == "symbol" and token.value == "}":
                break  # the end of the class body
            if token.kind == "keyword" and token.value in SUBROUTINE_KINDS:
                # <kind> <returnType> <name> ( ... ) { ... }
                kinds[tokens[index + 2].value] = token.value
                index = self._skip_subroutine_body(index + 3)
                continue
            index += 1

        return kinds

    def _skip_subroutine_body(self, index: int) -> int:
        """Return the index just behind the body of a subroutine."""
        tokens = self.tk.tokens
        total = len(tokens)

        # skip the parameter list
        while index < total and tokens[index].value != "(":
            index += 1
        depth = 0
        while index < total:
            if tokens[index].value == "(":
                depth += 1
            elif tokens[index].value == ")":
                depth -= 1
                if depth == 0:
                    index += 1
                    break
            index += 1

        # skip the body
        depth = 0
        while index < total:
            if tokens[index].value == "{":
                depth += 1
            elif tokens[index].value == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1

        return index

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    def _current_line(self) -> int:
        """The line of the token the cursor currently points at."""
        token = self.tk.peek(0)
        if token is None:
            return self.tk.tokens[-1].line if self.tk.tokens else 0
        return token.line

    def _peek_symbol(self, value: str) -> bool:
        """``True`` if the next token is the given symbol."""
        token = self.tk.peek(0)
        return token is not None and token.kind == "symbol" and token.value == value

    def _peek_keyword(self, *values: str) -> bool:
        """``True`` if the next token is one of the given keywords."""
        token = self.tk.peek(0)
        return token is not None and token.kind == "keyword" and token.value in values

    def _expect_symbol(self, value: str) -> None:
        """Consume the next token, which has to be the given symbol."""
        token = self.tk.advance()
        if token.kind != "symbol" or token.value != value:
            raise JackCompilationError(
                f"line {token.line}: expected '{value}' but found '{token.value}'"
            )

    def _expect_keyword(self, value: str) -> None:
        """Consume the next token, which has to be the given keyword."""
        token = self.tk.advance()
        if token.kind != "keyword" or token.value != value:
            raise JackCompilationError(
                f"line {token.line}: expected '{value}' but found '{token.value}'"
            )

    def _expect_identifier(self, what: str) -> str:
        """Consume the next token, which has to be an identifier."""
        token = self.tk.advance()
        if token.kind != "identifier":
            raise JackCompilationError(
                f"line {token.line}: expected {what} but found '{token.value}'"
            )
        return token.value


# ==========================================================================
# SECTION 5 — FILE LEVEL DRIVER
# ==========================================================================


def compile_file(jack_path: Path) -> Path:
    """Compile one ``.jack`` file into the sibling ``.vm`` file."""
    jack_path = Path(jack_path)
    # "utf-8-sig" transparently removes a byte order mark, which the
    # reference compiler does not accept
    text = jack_path.read_text(encoding="utf-8-sig")

    tokenizer = JackTokenizer(text)
    writer = VMWriter()
    engine = CompilationEngine(tokenizer, writer)
    engine.compile_class()

    vm_path = jack_path.with_suffix(".vm")
    vm_path.write_text(writer.output(), encoding="utf-8")
    return vm_path


def compile_path(target: Path) -> List[Path]:
    """Compile a ``.jack`` file or every ``.jack`` file of a folder."""
    target = Path(target)

    if target.is_dir():
        jack_files = sorted(target.glob("*.jack"))
        if not jack_files:
            raise JackCompilationError(f"no .jack file found in {target}")
        return [compile_file(path) for path in jack_files]

    if target.is_file():
        return [compile_file(target)]

    raise JackCompilationError(f"no such file or folder: {target}")


def main(argv: List[str]) -> int:
    """Entry point: ``python jack_compiler.py <file.jack | folder>``."""
    if len(argv) != 2:
        print(f"Usage: {Path(argv[0]).name} <file.jack | folder>", file=sys.stderr)
        print(
            "       A single .jack file is compiled into the matching .vm file\n"
            "       next to it.  A folder is compiled file by file, every .jack\n"
            "       file producing a .vm file of the same name.",
            file=sys.stderr,
        )
        return 1

    target = Path(argv[1])
    try:
        vm_paths = compile_path(target)
    except JackCompilationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for vm_path in vm_paths:
        print(f"wrote {vm_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
