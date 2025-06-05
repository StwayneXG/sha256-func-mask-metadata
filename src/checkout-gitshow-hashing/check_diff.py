import os
import re
import logging
import argparse
import pandas as pd
from collections import defaultdict, namedtuple

# --------------------------------------------------
# Configure script_logger once at module level
# --------------------------------------------------
script_logger = logging.getLogger("diff_processor")
script_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
script_logger.addHandler(handler)

# --------------------------------------------------
# A small namedtuple to hold “context” information
# for classes/enums/methods/fields we find in the
# original Java files.
# --------------------------------------------------
_Context = namedtuple(
    "_Context",
    [
        "type",         # "package", "class", "enum", "method", or "member_variable"
        "name",         # e.g. "Foo", "doSomething", "count"
        "start_line",   # line number (1-based) in the original file
        "end_line",     # inclusive: where this block ends
        "signature",    # the raw declaration line (trimmed)
        "parent"        # reference to parent _Context or None
    ],
)

class MethodExtractor:
    """
    We bring in your existing helpers for:
      - is_function_line()
      - extract_method_name()
      - is_member_variable()
      - extract_variable_name()
    """

    @staticmethod
    def is_function_line(line: str) -> bool:
        trimmed = line.strip()
        if trimmed.startswith("//"):
            return False
        # Detect class/enum declarations as “functions” here as well,
        # so that extract_method_name can pull out the class name.
        if "(" in trimmed and any(k in trimmed for k in ["private", "protected", "public", "static", "void"]):
            # But not a field (fields have ‘;’ at end without ‘=’ before it).
            if trimmed.endswith(";"):
                return False
            pre_comment = trimmed.split("//")[0].strip()
            if "=" in pre_comment:
                return False
            return True
        # Also consider “class Xyz” or “enum Xyz” lines as “function‐like” for name‐extraction:
        if "class " in trimmed or " enum " in trimmed or trimmed.startswith("class ") or trimmed.startswith("enum "):
            if trimmed.endswith(";"):
                return False
            pre_comment = trimmed.split("//")[0].strip()
            if "=" in pre_comment:
                return False
            return True
        return False

    @staticmethod
    def extract_method_name(line: str) -> str:
        """
        If this is a class/enum declaration, return the class name.
        Else if it’s a method or constructor, return the method name.
        Else return None.
        """
        trimmed = line.strip()
        # 1) Class or enum
        m_class = re.search(r'\b(class|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
        if m_class:
            return m_class.group(2)

        # 2) Otherwise scan for method/constructor name before '('
        m_method = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', trimmed)
        if m_method:
            return m_method.group(1)

        return None

    @staticmethod
    def is_member_variable(line: str) -> bool:
        """
        True if this looks like a field declaration
        (e.g. “private int count;”), false if it's a method.
        """
        stripped = line.strip()
        if any(k in stripped for k in ["private", "protected", "public", "static"]):
            # If it has “( )” and no “=” → likely a method, not a field
            if "(" in stripped and ")" in stripped and "=" not in stripped:
                return False
            # Fields must end in semicolon
            if stripped.endswith(";"):
                return True
        return False

    @staticmethod
    def extract_variable_name(line: str) -> str:
        """
        If there’s an “=”, split on “=” and take left side.
        Split on whitespace or commas and return the last token.
        """
        stripped = line.strip()
        if "=" in stripped:
            left = stripped.split("=")[0].strip()
        else:
            left = stripped
        tokens = left.replace(",", " ").split()
        return tokens[-1] if tokens else None

class DiffProcessor:
    """
    Encapsulates:
      1) Parsing a unified diff into “changed lines” per file.
      2) Loading the original Java file to build a “context map” of
         package / classes / enums / methods / fields.
      3) Grouping each changed line under the appropriate element.
      4) Returning a DataFrame with one row per changed element.
    """

    def __init__(self, repo_root: str):
        """
        repo_root: path to Git checkout root, so we can open the original .java files
        """
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        """
        Main entry point.  Given a unified diff string (no a/ or b/ prefixes),
        return a DataFrame with columns:
          - file_path
          - package
          - class_name
          - element_type    (class | enum | method | member_variable | method_body)
          - element_name
          - change_type     (added | removed | modified)
          - diff_lines      (Python list of all raw “+…” or “-…” lines for that element)
        """
        # 1) Parse the diff_text into per-file changed lines:
        per_file_changes = self._collect_changed_lines(diff_text)

        # 2) For each file that changed, build “contexts” from the original Java on disk:
        file_to_contexts = {}
        for fp in per_file_changes:
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = self._build_contexts_for_file(full_disk)

        # 3) Group changed lines by (class_name, element_type, element_name)
        grouped = {}
        for fp, changes in per_file_changes.items():
            contexts = file_to_contexts.get(fp, [])
            grouped[fp] = self._group_changes_for_file(fp, changes, contexts)

        # 4) Flatten grouped → list of records → DataFrame
        records = []
        for fp, groups in grouped.items():
            for (class_name, elem_type, elem_name), info in groups.items():
                pkg = info["package"]
                cset = info["change_types"]
                if cset == {"added"}:
                    cfinal = "added"
                elif cset == {"removed"}:
                    cfinal = "removed"
                else:
                    cfinal = "modified"
                records.append({
                    "file_path": fp,
                    "package": pkg,
                    "class_name": class_name,
                    "element_type": elem_type,
                    "element_name": elem_name,
                    "change_type": cfinal,
                    "diff_lines": info["diff_lines"]
                })

        df = pd.DataFrame(records, columns=[
            "file_path", "package", "class_name",
            "element_type", "element_name",
            "change_type", "diff_lines"
        ])
        return df

    def _collect_changed_lines(self, diff_text: str):
        """
        Step 1: Scan diff_text line by line.  Whenever we see “+++ path/to/File.java”,
        set current_file = that path.  Then whenever we see a hunk header “@@ -a,b +c,d @@”,
        track running old_line and new_line.  For each ‘-’ or ‘+’ line, record
        (old_lineno or new_lineno) and the raw line.  Skip any “import …” lines.
        """
        per_file_changes = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False

        hunk_re = re.compile(r'^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@')

        for raw in diff_text.splitlines():
            if raw.startswith("+++ "):
                path = raw[4:].strip()
                if path != "/dev/null":
                    current_file = path
                    per_file_changes[current_file] = []
                else:
                    script_logger.warning(f"Entire file deleted or /dev/null: {raw}")
                    current_file = None
                in_hunk = False
                continue
            if raw.startswith("--- "):
                continue

            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1))
                running_new = int(m.group(2))
                in_hunk = True
                continue

            if not in_hunk or not current_file:
                continue

            if raw.startswith(" "):
                running_old += 1
                running_new += 1
                continue

            prefix = raw[0]
            if prefix == "-":
                # Deletion: old_lineno = running_old
                if not raw.lstrip().startswith("- import "):
                    per_file_changes[current_file].append({
                        "old_lineno": running_old,
                        "new_lineno": None,
                        "raw": raw
                    })
                else:
                    script_logger.debug(f"Ignoring import‐line removal: {raw[1:].strip()}")
                running_old += 1
            elif prefix == "+":
                # Addition: new_lineno = running_new
                if not raw.lstrip().startswith("+ import "):
                    per_file_changes[current_file].append({
                        "old_lineno": None,
                        "new_lineno": running_new,
                        "raw": raw
                    })
                else:
                    script_logger.debug(f"Ignoring import‐line addition: {raw[1:].strip()}")
                running_new += 1
            else:
                # Unexpected (treat as context)
                script_logger.debug(f"Unexpected line in hunk: {raw}")
                # Don’t increment counters to avoid drift

        return per_file_changes

    def _build_contexts_for_file(self, full_path: str):
        """
        Step 2: Read the original Java file on disk (if it exists).  Build a list of
        _Context(type, name, start_line, end_line, signature, parent) for:
          - package (covers entire file)
          - each class/enum (find braces)
          - each method (find braces)
          - each member_variable (one‐line)
        Then link up parent pointers by nesting.  Return a list of _Context objects.
        """
        contexts = []
        if not os.path.isfile(full_path):
            script_logger.warning(f"File not found on disk (skipping contexts): {full_path}")
            return contexts

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        # 2a) Find package, if any
        pkg_name = ""
        for idx, ln in enumerate(lines, start=1):
            trimmed = ln.strip()
            if trimmed.startswith("package "):
                m = re.match(r'package\s+([\w\.]+)\s*;', trimmed)
                if m:
                    pkg_name = m.group(1)
                break

        # 2b) Collect declarations: (line_no, kind, name, signature)
        temp_decls = []
        for idx, ln in enumerate(lines, start=1):
            trimmed = ln.strip()
            if not trimmed or trimmed.startswith("//"):
                continue

            # CLASS / ENUM ?
            if " class " in trimmed or trimmed.startswith("class ") or \
               " enum " in trimmed or trimmed.startswith("enum "):
                m = re.search(r'\b(class|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
                if m:
                    kind = "enum" if m.group(1) == "enum" else "class"
                    name = m.group(2)
                    temp_decls.append((idx, kind, name, trimmed))
                    continue

            # METHOD ?
            if MethodExtractor.is_function_line(trimmed):
                # But skip if this is the class/enum line itself
                if (" class " in trimmed or trimmed.startswith("class ") or
                    " enum " in trimmed or trimmed.startswith("enum ")):
                    continue
                mname = MethodExtractor.extract_method_name(trimmed)
                if mname:
                    temp_decls.append((idx, "method", mname, trimmed))
                continue

            # MEMBER_VARIABLE ?
            if MethodExtractor.is_member_variable(trimmed):
                varname = MethodExtractor.extract_variable_name(trimmed)
                if varname:
                    temp_decls.append((idx, "member_variable", varname, trimmed))
                continue

        # 2c) For each decl, find its end_line by matching braces (for class/method).
        for (dline, kind, name, sig) in temp_decls:
            if kind in ("class", "enum"):
                # Find the line with the first '{'
                brace_line = None
                brace_count = 0
                if "{" in sig:
                    brace_line = dline
                    brace_count = sig.count("{") - sig.count("}")
                else:
                    # scan forward until we see '{'
                    for j in range(dline + 1, len(lines) + 1):
                        if "{" in lines[j - 1]:
                            brace_line = j
                            brace_count = lines[j - 1].count("{") - lines[j - 1].count("}")
                            break
                    if brace_line is None:
                        script_logger.warning(f"Could not find '{{' for {kind} {name} at line {dline} in {full_path}")
                        contexts.append(_Context(kind, name, dline, dline, sig, None))
                        continue

                end_line = brace_line
                for k in range(brace_line + 1, len(lines) + 1):
                    brace_count += lines[k - 1].count("{")
                    brace_count -= lines[k - 1].count("}")
                    if brace_count == 0:
                        end_line = k
                        break
                if brace_count != 0:
                    script_logger.warning(f"Braces never closed for {kind} {name} starting at line {dline}")
                    end_line = len(lines)

                contexts.append(_Context(kind, name, dline, end_line, sig, None))

            elif kind == "method":
                # Similar approach: find first '{', then match until '}'
                brace_line = None
                brace_count = 0
                if "{" in sig:
                    brace_line = dline
                    brace_count = sig.count("{") - sig.count("}")
                else:
                    for j in range(dline + 1, len(lines) + 1):
                        if "{" in lines[j - 1]:
                            brace_line = j
                            brace_count = lines[j - 1].count("{") - lines[j - 1].count("}")
                            break
                    if brace_line is None:
                        # e.g. interface method or abstract—no body
                        contexts.append(_Context("method", name, dline, dline, sig, None))
                        continue

                end_line = brace_line
                for k in range(brace_line + 1, len(lines) + 1):
                    brace_count += lines[k - 1].count("{")
                    brace_count -= lines[k - 1].count("}")
                    if brace_count == 0:
                        end_line = k
                        break
                if brace_count != 0:
                    script_logger.warning(f"Braces never closed for method {name} starting at line {dline}")
                    end_line = len(lines)

                contexts.append(_Context("method", name, dline, end_line, sig, None))

            elif kind == "member_variable":
                # No braces—one‐line field
                contexts.append(_Context("member_variable", name, dline, dline, sig, None))

            else:
                # Shouldn't really happen
                contexts.append(_Context(kind, name, dline, dline, sig, None))

        # 2d) Sort contexts by start_line, then assign parent pointers
        contexts.sort(key=lambda c: c.start_line)
        for i, ctx in enumerate(contexts):
            parent_candidate = None
            for prior in contexts[:i]:
                if prior.start_line <= ctx.start_line <= prior.end_line:
                    # prior fully contains ctx
                    if (parent_candidate is None or
                        (prior.start_line >= parent_candidate.start_line and
                         prior.end_line <= parent_candidate.end_line)):
                        parent_candidate = prior
            if parent_candidate:
                contexts[i] = _Context(ctx.type, ctx.name, ctx.start_line, ctx.end_line, ctx.signature, parent_candidate)

        # 2e) Add package-level context spanning entire file
        if pkg_name:
            contexts.insert(0, _Context("package", pkg_name, 1, len(lines), f"package {pkg_name}", None))

        return contexts

    def _group_changes_for_file(self, file_path, changes, contexts):
        """
        Step 3: Given a list of changed lines (with old_lineno/new_lineno/raw),
        and a list of _Context objects, assign each changed line to the proper
        (class_name, element_type, element_name).  Return a dict mapping:
           (class_name, element_type, element_name) → {
               "package": ...,
               "class_name": ...,
               "element_type": ...,
               "element_name": ...,
               "change_types": set(...),
               "diff_lines": [ ...raw lines ... ]
           }
        """
        def find_deepest_context(line_no):
            candidates = [c for c in contexts if c.start_line <= line_no <= c.end_line]
            return max(candidates, key=lambda c: c.start_line) if candidates else None

        grouped = {}
        for item in changes:
            old_ln = item["old_lineno"]
            new_ln = item["new_lineno"]
            raw_line = item["raw"]
            change_type = "added" if raw_line.startswith("+") else "removed"
            content = raw_line[1:].rstrip()
            stripped = content.strip()

            # 3.1) If this line is itself a class/enum declaration:
            if stripped.startswith("class ") or " class " in stripped or \
               stripped.startswith("enum ") or " enum " in stripped:
                cls_name = MethodExtractor.extract_method_name(stripped) or "N/A"
                pkg_ctx = next((c for c in contexts if c.type == "package"), None)
                pkg = pkg_ctx.name if pkg_ctx else ""
                key = (cls_name, "class", cls_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": cls_name,
                        "element_type": "class",
                        "element_name": cls_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)
                continue

            # 3.2) If this line is a method declaration:
            if MethodExtractor.is_function_line(stripped) and "(" in stripped:
                mname = MethodExtractor.extract_method_name(stripped) or "N/A"
                # Figure out which class this belongs to (in original).  If added, new_ln exists; else old_ln
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ("class", "enum", "package"):
                    parent = parent.parent
                class_name = parent.name if (parent and parent.type in ("class", "enum")) else ""
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type == "package") else ""
                key = (class_name, "method", mname)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": "method",
                        "element_name": mname,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)
                continue

            # 3.3) If this line is a field declaration:
            if MethodExtractor.is_member_variable(stripped):
                varname = MethodExtractor.extract_variable_name(stripped) or "N/A"
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ("class", "enum", "package"):
                    parent = parent.parent
                class_name = parent.name if (parent and parent.type in ("class", "enum")) else ""
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type == "package") else ""
                key = (class_name, "member_variable", varname)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": "member_variable",
                        "element_name": varname,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)
                continue

            # 3.4) Otherwise it’s a “body” change inside some method or class:
            lookup_ln = old_ln if old_ln is not None else new_ln
            ctx = find_deepest_context(lookup_ln) if lookup_ln else None
            if not ctx:
                script_logger.warning(f"No enclosing context for diff‐line {raw_line!r} in {file_path}. Marking as N/A.")
                class_name = ""
                pkg = ""
                element_type = "method_body"
                element_name = "N/A"
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": element_type,
                        "element_name": element_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)
                continue

            # Find outermost (method / member_variable / class / enum)
            c = ctx
            while c and c.type not in ("method", "member_variable", "class", "enum"):
                c = c.parent

            if not c:
                # We’re somewhere inside a context that is not a method or field,
                # treat as method_body under that class
                # e.g. inside a static block or initializer block
                parent = ctx
                while parent and parent.type not in ("class", "enum"):
                    parent = parent.parent
                class_name = parent.name if (parent and parent.type in ("class", "enum")) else ""
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=="package") else ""
                element_type = "method_body"
                element_name = "N/A"
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": element_type,
                        "element_name": element_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)
                continue

            # c is a method / member_variable / class / enum
            if c.type == "method":
                # all changed lines inside this method (signature or body) group here
                mc = c
                parent = mc.parent
                while parent and parent.type not in ("class", "enum"):
                    parent = parent.parent
                class_name = parent.name if (parent and parent.type in ("class","enum")) else ""
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=="package") else ""
                element_type = "method"
                element_name = mc.name
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": element_type,
                        "element_name": element_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)

            elif c.type == "member_variable":
                # If the changed line is exactly the field’s declaration, we group under field.
                # Otherwise (e.g. inside an initializer), treat as method_body.
                if c.start_line == lookup_ln and stripped.startswith(c.signature.strip()):
                    mc = c
                    parent = mc.parent
                    while parent and parent.type not in ("class","enum"):
                        parent = parent.parent
                    class_name = parent.name if (parent and parent.type in ("class","enum")) else ""
                    pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=="package") else ""
                    element_type = "member_variable"
                    element_name = mc.name
                    key = (class_name, element_type, element_name)
                    if key not in grouped:
                        grouped[key] = {
                            "package": pkg,
                            "class_name": class_name,
                            "element_type": element_type,
                            "element_name": element_name,
                            "change_types": set(),
                            "diff_lines": []
                        }
                    grouped[key]["change_types"].add(change_type)
                    grouped[key]["diff_lines"].append(raw_line)
                else:
                    # inside a field’s initializer; treat as method_body
                    parent = c.parent
                    while parent and parent.type not in ("class","enum"):
                        parent = parent.parent
                    class_name = parent.name if (parent and parent.type in ("class","enum")) else ""
                    pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=="package") else ""
                    element_type = "method_body"
                    element_name = "N/A"
                    key = (class_name, element_type, element_name)
                    if key not in grouped:
                        grouped[key] = {
                            "package": pkg,
                            "class_name": class_name,
                            "element_type": element_type,
                            "element_name": element_name,
                            "change_types": set(),
                            "diff_lines": []
                        }
                    grouped[key]["change_types"].add(change_type)
                    grouped[key]["diff_lines"].append(raw_line)

            elif c.type in ("class", "enum"):
                # A change inside a class but not in any method or field.
                class_name = c.name
                pkg = c.parent.name if (c.parent and c.parent.type=="package") else ""
                element_type = "method_body"
                element_name = "N/A"
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": element_type,
                        "element_name": element_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)

            else:
                # Shouldn't happen, but just in case:
                script_logger.warning(f"Unhandled context type {c.type} for line: {raw_line!r}")
                class_name = ""
                pkg = ""
                element_type = "method_body"
                element_name = "N/A"
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        "package": pkg,
                        "class_name": class_name,
                        "element_type": element_type,
                        "element_name": element_name,
                        "change_types": set(),
                        "diff_lines": []
                    }
                grouped[key]["change_types"].add(change_type)
                grouped[key]["diff_lines"].append(raw_line)

        return grouped

# --------------------------------------------------
# DRIVER CODE
# --------------------------------------------------
if __name__ == "__main__":
    """
    Example usage from the command line:
    
      python diff_processor.py \
        --diff-file /path/to/some.diff \
        --repo-root /path/to/my/project \
        --out-csv changes.csv
    
    This will:
      1) Read the diff from --diff-file
      2) Build the DataFrame of changed elements
      3) Print the first few rows to stdout
      4) If --out-csv is provided, write the full DataFrame to that CSV
    """
    parser = argparse.ArgumentParser(
        description="Parse a unified diff and output a DataFrame of changed Java elements."
    )
    parser.add_argument(
        "--diff-file",
        required=True,
        help="Path to a unified diff (e.g. output of `git show --no-prefix -U500`)."
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Path to the Git checkout root (so we can open original .java files)."
    )
    parser.add_argument(
        "--out-csv",
        required=False,
        help="Optional: write the resulting DataFrame to this CSV file."
    )
    args = parser.parse_args()

    if not os.path.isfile(args.diff_file):
        script_logger.error(f"Cannot find diff file: {args.diff_file}")
        exit(1)
    if not os.path.isdir(args.repo_root):
        script_logger.error(f"Cannot find repo root directory: {args.repo_root}")
        exit(1)

    with open(args.diff_file, "r", encoding="utf-8") as f:
        diff_text = f.read()

    processor = DiffProcessor(repo_root=args.repo_root)
    df = processor.parse_diff_to_dataframe(diff_text)

    # Show the first few rows on the console
    script_logger.info("Parsed diff into DataFrame:")
    print(df.head(10).to_string(index=False))

    # Optionally write out CSV
    if args.out_csv:
        try:
            df.to_csv(args.out_csv, index=False)
            script_logger.info(f"Wrote full results to CSV: {args.out_csv}")
        except Exception as e:
            script_logger.error(f"Failed to write CSV to {args.out_csv}: {e}")
