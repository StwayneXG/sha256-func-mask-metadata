import os
import re
import pandas as pd
from typing import List, Dict

class JavaContextExtractor:
    """
    - Contains helper methods to detect class/interface/enum/method/field lines.
    - Provides a single entry point, `extract_context`, which reads a .java file
      line by line and returns a list of declaration‐dicts without extra fields.
    """

    @staticmethod
    def _is_function_line(line: str) -> bool:
        """Return True if 'line' looks like a method/constructor or class/interface/enum declaration."""
        trimmed = line.strip()
        if not trimmed:
            return False
        # Method or constructor: must have “(” and one of the visibility/return keywords
        if "(" in trimmed and any(k in trimmed for k in ["private", "protected", "public", "static", "void"]):
            # Exclude field‐style lines that end with ";" and contain no "="
            if trimmed.endswith(";"):
                return False
            if "=" in trimmed.split("//")[0].strip():
                return False
            return True

        # Class/Interface/Enum: look for the keywords, not ending in “;”, not an assignment
        if (
            " class " in trimmed or trimmed.startswith("class ")
            or " interface " in trimmed or trimmed.startswith("interface ")
            or " enum " in trimmed or trimmed.startswith("enum ")
        ):
            if trimmed.endswith(";"):
                return False
            if "=" in trimmed.split("//")[0].strip():
                return False
            return True

        return False

    @staticmethod
    def _extract_method_name(line: str) -> str:
        """Given a declaration line, return the identifier for a class/enum/interface or a method/constructor."""
        trimmed = line.strip()

        # Case 1: class / interface / enum
        m_type = re.search(r'\b(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
        if m_type:
            return m_type.group(2)

        # Case 2: method or constructor name (look for “name(”)
        m_method = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', trimmed)
        if m_method:
            return m_method.group(1)

        return None

    @staticmethod
    def _is_member_variable(line: str) -> bool:
        """Return True if this line looks like a field declaration (outside of a method body)."""
        stripped = line.strip()
        if any(k in stripped for k in ["private", "protected", "public", "static"]):
            # Exclude methods—if it has "(" and ")" and no "=" then it’s probably a method signature
            if "(" in stripped and ")" in stripped and "=" not in stripped:
                return False
            # Must end with a semicolon for a field
            if stripped.endswith(";"):
                return True
        return False

    @staticmethod
    def _extract_variable_name(line: str) -> str:
        """Return just the field’s name (last token before ‘=’ or before ‘;’)."""
        stripped = line.strip()
        if "=" in stripped:
            left = stripped.split("=")[0].strip()
        else:
            left = stripped[:-1].strip()  # drop trailing “;”
        tokens = left.replace(",", " ").split()
        return tokens[-1] if tokens else None

    @staticmethod
    def _find_body_end(start_line_idx: int, start_col_idx: int, lines: List[str]) -> int:
        """
        Starting at (start_line_idx, start_col_idx) in `lines`,
        scan forward (accounting for { } balance, ignoring comments/strings)
        until the matching closing brace. Return the 1-based line index of that ‘}’.
        """
        total_lines = lines
        brace_count = 0
        in_string = False
        in_char = False
        in_block_comment = False
        escape_next = False
        current_line = start_line_idx
        current_col = start_col_idx

        while current_line < len(total_lines):
            line = total_lines[current_line]
            i = current_col if current_line == start_line_idx else 0
            in_line_comment = False

            while i < len(line):
                char = line[i]
                if escape_next:
                    escape_next = False
                elif char == "\\":
                    escape_next = True
                elif in_string:
                    if char == '"':
                        in_string = False
                elif in_char:
                    if char == "'":
                        in_char = False
                elif in_line_comment:
                    pass
                elif in_block_comment:
                    if char == '*' and i + 1 < len(line) and line[i + 1] == '/':
                        in_block_comment = False
                        i += 1
                else:
                    if char == '"':
                        in_string = True
                    elif char == "'":
                        in_char = True
                    elif char == '/' and i + 1 < len(line):
                        if line[i + 1] == '/':
                            in_line_comment = True
                            i += 1
                        elif line[i + 1] == '*':
                            in_block_comment = True
                            i += 1
                    elif char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # Found the matching “}” for the initial opening “{”
                            return current_line + 1  # return as 1-based index
                i += 1

            current_line += 1
            current_col = 0

        return None

    @classmethod
    def extract_context(cls, file_path: str) -> List[Dict]:
        """
        Read `file_path` line by line, strip comments (// and /* */), and extract:
          - package declaration
          - import statements
          - class/interface/enum declarations (with extends/implements)
          - method declarations (only name and span)
          - member variables (including multi-line declarations)

        Returns a list of dicts with keys:
          - element_name
          - type               in { "package", "import", "class", "interface", "enum",
                                 "method", "member_variable" }
          - start_line         (1-based)
          - end_line           (1-based; same as start_line for single‐line items)
          - extends            (list[str], only for class/interface/enum)
          - implements         (list[str], only for class/interface/enum)
        """
        context: List[Dict] = []
        in_block_comment = False
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        idx = 0
        while idx < len(lines):
            raw_line = lines[idx]
            line = raw_line

            # 1) If inside a block comment, skip until "*/"
            if in_block_comment:
                if "*/" in line:
                    line = line.split("*/", 1)[1]
                    in_block_comment = False
                else:
                    idx += 1
                    continue

            # 2) Strip any new /* */ blocks on this line
            while "/*" in line:
                start = line.find("/*")
                end = line.find("*/", start + 2)
                if end != -1:
                    line = line[:start] + line[end + 2:]
                else:
                    line = line[:start]
                    in_block_comment = True
                    break

            # 3) Strip inline //
            if "//" in line:
                line = line.split("//", 1)[0]

            stripped = line.strip()
            if not stripped:
                idx += 1
                continue

            # 4) package declaration
            if stripped.startswith("package ") and stripped.endswith(";"):
                pkg_name = stripped[len("package "):-1].strip()
                context.append({
                    "element_name": pkg_name,
                    "type": "package",
                    "start_line": idx + 1,
                    "end_line": idx + 1,
                    "extends": [],
                    "implements": []
                })
                idx += 1
                continue

            # 5) import statement
            if stripped.startswith("import ") and stripped.endswith(";"):
                imp_name = stripped[len("import "):-1].strip()
                context.append({
                    "element_name": imp_name,
                    "type": "import",
                    "start_line": idx + 1,
                    "end_line": idx + 1,
                    "extends": [],
                    "implements": []
                })
                idx += 1
                continue

            # 6) Multi-line member variable (starts with visibility/static and contains "=", not ending with ";")
            if any(k in stripped for k in ["private", "protected", "public", "static"]) \
               and "=" in stripped and not stripped.endswith(";") \
               and not cls._is_function_line(stripped):
                # This is start of a multi-line field. Find semicolon end.
                start_idx = idx
                var_line = stripped
                # Extract variable name from this start line
                var_name = cls._extract_variable_name(stripped)

                # Now find the line where declaration ends (first ";" outside comments)
                end_idx = idx
                while end_idx < len(lines):
                    check_line = lines[end_idx]
                    # Strip comments from check_line
                    temp = check_line
                    if "/*" in temp:
                        temp = temp.split("/*", 1)[0]
                    if "//" in temp:
                        temp = temp.split("//", 1)[0]
                    if ";" in temp:
                        break
                    end_idx += 1

                context.append({
                    "element_name": var_name,
                    "type": "member_variable",
                    "start_line": start_idx + 1,
                    "end_line": end_idx + 1,
                    "extends": [],
                    "implements": []
                })
                idx = end_idx + 1
                continue

            # 7) Single-line member variable
            if cls._is_member_variable(stripped):
                var_name = cls._extract_variable_name(stripped)
                context.append({
                    "element_name": var_name,
                    "type": "member_variable",
                    "start_line": idx + 1,
                    "end_line": idx + 1,
                    "extends": [],
                    "implements": []
                })
                idx += 1
                continue

            # 8) class / interface / enum / method declarations
            if cls._is_function_line(stripped):
                if re.search(r'\bclass \b', stripped):
                    decl_type = "class"
                elif re.search(r'\binterface \b', stripped):
                    decl_type = "interface"
                elif re.search(r'\benum \b', stripped):
                    decl_type = "enum"
                else:
                    decl_type = "method"

                elem_name = cls._extract_method_name(stripped)
                end_line = cls._find_body_end(idx, 0, lines) or (idx + 1)

                extends_list = []
                implements_list = []

                if decl_type in ("class", "interface", "enum"):
                    type_pattern = (
                        r'\b(?:class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)'
                        r'(?:\s+extends\s+([A-Za-z0-9_<>\.,\s]+))?'
                        r'(?:\s+implements\s+([A-Za-z0-9_<>\.,\s]+))?'
                    )
                    m = re.search(type_pattern, stripped)
                    if m:
                        elem_name = m.group(1)
                        if m.group(2):
                            extends_list = [e.strip() for e in m.group(2).split(",")]
                        if m.group(3):
                            implements_list = [i.strip() for i in m.group(3).split(",")]

                    context.append({
                        "element_name": elem_name,
                        "type": decl_type,
                        "start_line": idx + 1,
                        "end_line": end_line,
                        "extends": extends_list,
                        "implements": implements_list
                    })
                else:
                    context.append({
                        "element_name": elem_name,
                        "type": "method",
                        "start_line": idx + 1,
                        "end_line": end_line,
                        "extends": [],
                        "implements": []
                    })
                idx += 1
                continue

            idx += 1

        return context