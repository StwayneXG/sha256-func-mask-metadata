import os
import re
import logging
from collections import namedtuple

# Use the same logger as the main processor
script_logger = logging.getLogger("diff_processor")

# Define the Context namedtuple
_Context = namedtuple(
    "_Context",
    [
        "type",         # "package", "class", "enum", "method", or "member_variable"
        "name",         # e.g. "Foo", "doSomething", "count"
        "start_line",   # line number (1-based) in the original file
        "end_line",     # inclusive: where this block ends in the original file
        "signature",    # the raw declaration line (trimmed)
        "parent"        # reference to parent _Context or None
    ],
)


def build_contexts_for_file(full_path: str):
    """
    Read the Java file at full_path and return a list of _Context objects for:
      - the package (if any)
      - each class or enum (with their start/end brace lines)
      - each method (with their start/end brace lines)
      - each member variable (single line)

    This version uses a comment‐aware brace matcher to avoid counting braces inside comments or strings.
    """
    contexts = []
    if not os.path.isfile(full_path):
        script_logger.warning(f"File not found on disk (skipping contexts): {full_path}")
        return contexts

    # Read entire file
    with open(full_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    content = "\n".join(lines)

    # 1) Find package statement (if any)
    pkg_name = ""
    for idx, ln in enumerate(lines, start=1):
        trimmed = ln.strip()
        if trimmed.startswith("package "):
            m = re.match(r'package\s+([\w\.]+)\s*;', trimmed)
            if m:
                pkg_name = m.group(1)
            break

    # 2) Collect declarations: (line_no, kind, name, signature)
    temp_decls = []  # (line_no, kind, name, signature)
    from method_extractor import MethodExtractor

    for idx, ln in enumerate(lines, start=1):
        trimmed = ln.strip()
        if not trimmed or trimmed.startswith("//"):
            continue

        # 2a) class or enum?
        if " class " in trimmed or trimmed.startswith("class ") or \
           " enum " in trimmed or trimmed.startswith("enum "):
            m = re.search(r'\b(class|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
            if m:
                kind = "enum" if m.group(1) == "enum" else "class"
                name = m.group(2)
                temp_decls.append((idx, kind, name, trimmed))
                continue

        # 2b) method or constructor?
        if MethodExtractor.is_function_line(trimmed):
            if (" class " in trimmed or trimmed.startswith("class ") or
                " enum " in trimmed or trimmed.startswith("enum ")):
                continue
            mname = MethodExtractor.extract_method_name(trimmed)
            if mname:
                temp_decls.append((idx, "method", mname, trimmed))
            continue

        # 2c) member variable?
        if MethodExtractor.is_member_variable(trimmed):
            varname = MethodExtractor.extract_variable_name(trimmed)
            if varname:
                temp_decls.append((idx, "member_variable", varname, trimmed))
            continue

    # 3) For each decl, find its end_line
    for (dline, kind, name, sig) in temp_decls:
        if kind in ("class", "enum"):
            # Locate the '{'
            brace_idx = sig.find('{')
            if brace_idx >= 0:
                end = MethodExtractor.find_body_end(dline - 1, brace_idx, lines)
                if end is None:
                    script_logger.warning(f"Braces never closed for {kind} {name} starting at line {dline}")
                    end = len(lines)
                contexts.append(_Context(kind, name, dline, end, sig, None))
            else:
                # Scan forward for '{'
                found = False
                for j in range(dline, len(lines)):
                    line_j = lines[j]
                    idx_br = line_j.find('{')
                    if idx_br >= 0:
                        end = MethodExtractor.find_body_end(j, idx_br, lines)
                        if end is None:
                            script_logger.warning(f"Braces never closed for {kind} {name} starting at line {dline}")
                            end = len(lines)
                        contexts.append(_Context(kind, name, dline, end, sig, None))
                        found = True
                        break
                if not found:
                    script_logger.warning(f"Could not find '{{' for {kind} {name} at line {dline} in {full_path}")
                    contexts.append(_Context(kind, name, dline, dline, sig, None))

        elif kind == "method":
            # Locate '{' in signature
            brace_idx = sig.find('{')
            if brace_idx >= 0:
                end = MethodExtractor.find_body_end(dline - 1, brace_idx, lines)
                if end is None:
                    script_logger.warning(f"Braces never closed for method {name} starting at line {dline}")
                    end = len(lines)
                contexts.append(_Context("method", name, dline, end, sig, None))
            else:
                # Scan forward for '{'
                found = False
                for j in range(dline, len(lines)):
                    line_j = lines[j]
                    idx_br = line_j.find('{')
                    if idx_br >= 0:
                        end = MethodExtractor.find_body_end(j, idx_br, lines)
                        if end is None:
                            script_logger.warning(f"Braces never closed for method {name} starting at line {dline}")
                            end = len(lines)
                        contexts.append(_Context("method", name, dline, end, sig, None))
                        found = True
                        break
                if not found:
                    # Probably abstract/interface method with no body
                    contexts.append(_Context("method", name, dline, dline, sig, None))

        elif kind == "member_variable":
            contexts.append(_Context("member_variable", name, dline, dline, sig, None))

        else:
            contexts.append(_Context(kind, name, dline, dline, sig, None))

    # 4) Sort contexts by start_line and assign parent pointers
    contexts.sort(key=lambda c: c.start_line)
    for i, ctx in enumerate(contexts):
        parent_candidate = None
        for prior in contexts[:i]:
            if prior.start_line <= ctx.start_line <= prior.end_line:
                if (parent_candidate is None or
                    (prior.start_line >= parent_candidate.start_line and
                     prior.end_line <= parent_candidate.end_line)):
                    parent_candidate = prior
        if parent_candidate:
            contexts[i] = _Context(ctx.type, ctx.name, ctx.start_line, ctx.end_line, ctx.signature, parent_candidate)

    # 5) Add package-level context that spans entire file
    if pkg_name:
        contexts.insert(0, _Context("package", pkg_name, 1, len(lines), f"package {pkg_name}", None))

    return contexts
