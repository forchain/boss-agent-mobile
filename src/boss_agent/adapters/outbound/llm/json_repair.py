"""
boss_agent.adapters.outbound.llm.json_repair
============================================
The tolerant JSON recovery chain shared by every LLM protocol client.

LLM output arrives as prose, markdown fences, or a token-truncated object. The repair
that rescues it is a property of the model, not of the wire protocol, so both clients
share one implementation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from boss_agent.core.shared.errors import LLMError


class JSONRepairMixin:
    """Multi-tier recovery for malformed JSON from an LLM."""

    @staticmethod
    def _extract_json_block(text: str) -> str:
        """Extract JSON substring from potentially markdown-wrapped LLM output."""
        text = text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            return text[brace_start : brace_end + 1]
        return text

    @staticmethod
    def _auto_close_json(raw: str) -> str:
        """Auto-close unclosed strings, arrays, and objects caused by token truncation."""
        s = raw.strip()
        s = re.sub(r',\s*"[^"]*"\s*:\s*$', "", s)
        s = re.sub(r',\s*"[^"]*$', "", s)
        s = re.sub(r",\s*$", "", s)

        in_string = False
        escape = False
        stack: list[str] = []

        for c in s:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c in "{[":
                    stack.append(c)
                elif c in "}]" and stack:
                    top = stack[-1]
                    if (c == "}" and top == "{") or (c == "]" and top == "["):
                        stack.pop()

        if in_string:
            s += '"'

        while stack:
            top = stack.pop()
            if top == "{":
                s += "}"
            elif top == "[":
                s += "]"

        return s

    @classmethod
    def _robust_parse_json(cls, json_str: str) -> dict[str, Any]:
        """Parse JSON with multi-tier recovery for trailing commas, unescaped inner
        quotes, newlines, and truncation."""
        raw = json_str.strip()

        # 1. Direct standard parse
        try:
            return json.loads(raw, strict=False)
        except Exception:
            pass

        # 2. Clean trailing commas before closing braces/brackets
        cleaned = re.sub(r",\s*([\]}])", r"\1", raw)
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            pass

        # 2.5 Repair arrays improperly containing key-value pairs instead of objects
        def fix_array_object_mixup(text: str) -> str:
            chars = list(text)
            n = len(chars)
            i = 0
            in_str = False
            escape = False
            stack: list[tuple[str, str]] = []

            while i < n:
                c = chars[i]
                if escape:
                    escape = False
                    i += 1
                    continue
                if c == "\\":
                    escape = True
                    i += 1
                    continue
                if c == '"':
                    in_str = not in_str
                    i += 1
                    continue
                if not in_str:
                    if c in "{[":
                        if c == "[":
                            j = i + 1
                            while j < n and chars[j] in " \t\r\n":
                                j += 1
                            match = re.match(r'^"[^"]+"\s*:', text[j:])
                            if match:
                                chars[i] = "{"
                                stack.append(("converted", "{"))
                                i += 1
                                continue
                        stack.append(("normal", c))
                    elif c in "}]":
                        if stack:
                            kind, open_c = stack.pop()
                            if kind == "converted" and c == "]":
                                chars[i] = "}"
                i += 1
            return "".join(chars)

        cleaned = fix_array_object_mixup(cleaned)
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            pass

        # 3. Sanitize unescaped control characters inside string literals
        def sanitize_string_control_chars(s: str) -> str:
            out: list[str] = []
            in_str = False
            escape = False
            for c in s:
                if escape:
                    out.append(c)
                    escape = False
                    continue
                if c == "\\":
                    out.append(c)
                    escape = True
                    continue
                if c == '"':
                    in_str = not in_str
                    out.append(c)
                    continue
                if in_str:
                    if c == "\n":
                        out.append("\\n")
                    elif c == "\r":
                        out.append("\\r")
                    elif c == "\t":
                        out.append("\\t")
                    else:
                        out.append(c)
                else:
                    out.append(c)
            return "".join(out)

        sanitized = sanitize_string_control_chars(cleaned)
        try:
            return json.loads(sanitized, strict=False)
        except Exception:
            pass

        # 4. Tokenizer-level unescaped quote repair
        def fix_unescaped_quotes_tokenizer(s: str) -> str:
            chars = list(s)
            i = 0
            in_string = False
            n = len(chars)
            res: list[str] = []
            while i < n:
                c = chars[i]
                if c == "\\":
                    res.append(c)
                    if i + 1 < n:
                        res.append(chars[i + 1])
                        i += 2
                        continue
                    i += 1
                    continue
                if c == '"':
                    if not in_string:
                        in_string = True
                        res.append(c)
                    else:
                        j = i + 1
                        while j < n and chars[j] in " \t\r\n":
                            j += 1
                        if j < n and chars[j] in ",:}]":
                            in_string = False
                            res.append(c)
                        else:
                            res.append('\\"')
                    i += 1
                    continue
                res.append(c)
                i += 1
            return "".join(res)

        fixed_quotes = fix_unescaped_quotes_tokenizer(sanitized)
        fixed_quotes = re.sub(r",\s*([\]}])", r"\1", fixed_quotes)
        try:
            return json.loads(fixed_quotes, strict=False)
        except Exception:
            pass

        # 5. Python AST literal eval (single quotes, Python booleans/None)
        try:
            import ast

            py_str = re.sub(r"\btrue\b", "True", fixed_quotes)
            py_str = re.sub(r"\bfalse\b", "False", py_str)
            py_str = re.sub(r"\bnull\b", "None", py_str)
            eval_result = ast.literal_eval(py_str)
            if isinstance(eval_result, dict):
                return eval_result
        except Exception:
            pass

        # 6. Auto-close truncated JSON structures
        closed = cls._auto_close_json(fixed_quotes)
        try:
            return json.loads(closed, strict=False)
        except Exception:
            pass

        closed_orig = cls._auto_close_json(raw)
        try:
            return json.loads(closed_orig, strict=False)
        except Exception as e:
            raise LLMError(f"Failed to decode LLM response into JSON: {raw}") from e


def extract_json_block(text: str) -> str:
    """Module-level form of the mixin's markdown-fence extraction."""
    return JSONRepairMixin._extract_json_block(text)


def robust_parse_json(text: str) -> dict[str, Any]:
    """Module-level form of the mixin's recovery chain."""
    return JSONRepairMixin._robust_parse_json(text)
