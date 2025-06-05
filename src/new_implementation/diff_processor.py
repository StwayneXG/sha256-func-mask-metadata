import os
import re
import logging
import pandas as pd
from collections import defaultdict

from method_extractor import MethodExtractor
from context_builder import build_contexts_for_file, _Context

script_logger = logging.getLogger("diff_processor")
logging.basicConfig(level=logging.DEBUG)


class DiffProcessor:
    """
    Parses a unified diff and produces a DataFrame of changed elements
    (import, class, method, or member_variable). Each row includes:
      - file_path
      - class_name
      - element_type   ('import' | 'class' | 'method' | 'member_variable')
      - element_name
      - change_type    ('added' | 'removed' | 'modified' for imports/fields; 
                        'completely_added' | 'completely_removed' | 'modified' for methods)
      - diff_lines     (list of raw '+...' and '-...' lines that belong to this element)
      - element_source (the on‐disk source; None if entirely removed)
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        script_logger.debug("Starting parse_diff_to_dataframe")
        per_file_changes = self._collect_changed_lines(diff_text)

        # Build contexts for every file we touched (classes/methods/fields)
        file_to_contexts = {}
        for fp in per_file_changes:
            script_logger.debug(f"Building contexts for file: {fp}")
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = build_contexts_for_file(full_disk)

        # Group changes per file
        grouped_per_file = {}
        for fp, changes in per_file_changes.items():
            contexts = file_to_contexts.get(fp, [])
            grouped_per_file[fp] = self._group_changes_for_file(fp, changes, contexts)

        # Flatten and extract source
        rows = []
        for fp, groups in grouped_per_file.items():
            full_disk = os.path.join(self.repo_root, fp)
            try:
                with open(full_disk, 'r', encoding='utf-8') as f:
                    file_lines = f.read().splitlines()
            except FileNotFoundError:
                file_lines = []
                script_logger.warning(f"Cannot open {full_disk} to extract source.")

            # Keep track of which classes need an extra "class" row
            classes_with_changes = set()

            # First, collect element rows
            for (class_name, elem_type, elem_name), info in groups.items():
                change_type = info["computed_change_type"]
                diff_lines = info["diff_lines"]
                ctx = info.get("context")

                # Element source logic
                if elem_type == "method":
                    if change_type == "completely_removed":
                        elem_src = None
                    else:
                        if ctx and file_lines:
                            start, end = ctx.start_line, ctx.end_line
                            elem_src = "\n".join(file_lines[start - 1 : end])
                        else:
                            elem_src = ""
                    classes_with_changes.add(class_name)
                elif elem_type == "member_variable":
                    if change_type == "removed":
                        elem_src = None
                    else:
                        if ctx and file_lines:
                            idx = ctx.start_line
                            elem_src = file_lines[idx - 1]
                        else:
                            elem_src = ""
                    classes_with_changes.add(class_name)
                elif elem_type == "import":
                    # Imports don’t live on disk in “new” version if removed
                    elem_src = None
                else:
                    # (We do not emit individual class rows here—only at the end.)
                    elem_src = ""

                rows.append(
                    {
                        "file_path": fp,
                        "class_name": class_name,
                        "element_type": elem_type,
                        "element_name": elem_name,
                        "change_type": change_type,
                        "diff_lines": diff_lines,
                        "element_source": elem_src,
                    }
                )

            # Next, for every class that had at least one change inside, emit a “class” row
            # with the entire class implementation. Mark change_type="modified" if there's any change.
            for (cname, kind, _) in groups.keys():
                if kind != "method" and kind != "member_variable":
                    continue  # skip non‐method/field keys
                if cname in classes_with_changes and cname:
                    # We only emit one row per class; guard with a set
                    # Need to find the matching class context from contexts list
                    file_contexts = file_to_contexts.get(fp, [])
                    class_ctx = next(
                        (c for c in file_contexts if c.type == "class" and c.name == cname), None
                    )
                    if not class_ctx:
                        continue
                    start, end = class_ctx.start_line, class_ctx.end_line
                    if file_lines:
                        class_src = "\n".join(file_lines[start - 1 : end])
                    else:
                        class_src = ""
                    rows.append(
                        {
                            "file_path": fp,
                            "class_name": cname,
                            "element_type": "class",
                            "element_name": cname,
                            "change_type": "modified",
                            "diff_lines": [],
                            "element_source": class_src,
                        }
                    )
                    # Remove from the set so we don’t duplicate
                    classes_with_changes.remove(cname)

        df = pd.DataFrame(
            rows,
            columns=[
                "file_path",
                "class_name",
                "element_type",
                "element_name",
                "change_type",
                "diff_lines",
                "element_source",
            ],
        )
        script_logger.debug("parse_diff_to_dataframe complete")
        return df

    def _collect_changed_lines(self, diff_text: str):
        """
        Parses the unified diff text into a dict:
          { file_path: [ { old_lineno, new_lineno, raw }, … ], … }
        Only Java files are kept. We skip any diff metadata, only record '-' and '+' lines.
        """
        script_logger.debug("Starting _collect_changed_lines")
        per_file = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        hunk_re = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

        for raw in diff_text.splitlines():
            script_logger.debug(f"Reading diff line: {raw}")

            # 1) diff header resets
            if raw.startswith("diff --git "):
                current_file = None
                in_hunk = False
                continue

            # 2) skip index metadata
            if raw.startswith("index "):
                continue

            # 3) skip “No newline” markers
            if raw.startswith("\\ No newline at end of file"):
                continue

            # 4) file path lines
            if raw.startswith("+++ "):
                path = raw[4:].strip()
                # only keep .java files
                if path.endswith(".java") and path != "/dev/null":
                    current_file = path
                    per_file[current_file] = []
                    script_logger.debug(f"Set current_file to {current_file}")
                else:
                    current_file = None
                in_hunk = False
                continue

            if raw.startswith("--- "):
                continue

            # 5) hunk header
            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1))
                running_new = int(m.group(2))
                in_hunk = True
                script_logger.debug(
                    f"Starting hunk for {current_file}: old={running_old}, new={running_new}"
                )
                continue

            if not in_hunk or not current_file:
                continue

            # 6) context line
            if raw.startswith(" "):
                running_old += 1
                running_new += 1
                continue

            # 7) removal
            if raw.startswith("-"):
                per_file[current_file].append(
                    {"old_lineno": running_old, "new_lineno": None, "raw": raw}
                )
                script_logger.debug(f"Recorded removal at {running_old}: {raw}")
                running_old += 1
                continue

            # 8) addition
            if raw.startswith("+"):
                per_file[current_file].append(
                    {"old_lineno": None, "new_lineno": running_new, "raw": raw}
                )
                script_logger.debug(f"Recorded addition at {running_new}: {raw}")
                running_new += 1
                continue

            # 9) anything else
            script_logger.debug(f"Unexpected line in hunk: {raw}")

        script_logger.debug("_collect_changed_lines complete")
        return per_file

    def _group_changes_for_file(self, file_path, changes, contexts):
        """
        Given:
          - file_path (e.g. "src/.../MyClass.java")
          - changes: list of {old_lineno, new_lineno, raw}
          - contexts: the list of _Context objects for that file (classes/methods/fields)

        We produce a grouped dict:
           { (className, element_type, element_name): { diff_lines, context, remove_sig, add_sig, body_changes, computed_change_type } }

        We handle the five element‐types:
          1) import
          2) class
          3) method (with complete addition/removal checks)
          4) member_variable
          5) body change inside a method
        """

        script_logger.debug(f"Starting _group_changes_for_file for {file_path}")

        def find_deepest_context(line_no):
            # Among all contexts whose [start_line..end_line] covers line_no,
            # return the one with greatest start_line (deepest nesting).
            cands = [c for c in contexts if c.start_line <= line_no <= c.end_line]
            return max(cands, key=lambda c: c.start_line) if cands else None

        grouped = {}
        # We need two sets for method complete-add / complete-remove detection:
        #   added_methods: keys whose complete body is added
        #   removed_methods: keys whose complete body is removed
        # Step 1: collect all “+” signature lines (potential complete_add) and “-” signature lines (potential complete_remove).
        plus_sigs = []
        minus_sigs = []
        for ch in changes:
            raw = ch["raw"]
            if raw.startswith("+"):
                stripped = raw[1:].strip()
                if MethodExtractor.is_function_line(stripped) and "(" in stripped:
                    plus_sigs.append((ch["new_lineno"], stripped))
            elif raw.startswith("-"):
                stripped = raw[1:].strip()
                if MethodExtractor.is_function_line(stripped) and "(" in stripped:
                    minus_sigs.append((ch["old_lineno"], stripped))

        # Step 2: Identify complete removals
        removed_method_ranges = {}  # key → (start,end) of entire method
        for old_ln, sig_str in minus_sigs:
            parent = find_deepest_context(old_ln)
            while parent and parent.type != "method":
                parent = parent.parent
            if not parent:
                continue
            class_name = parent.parent.name if parent.parent else ""
            method_name = parent.name
            key = (class_name, "method", method_name)
            start_ln, end_ln = parent.start_line, parent.end_line

            # Collect all old_lns that have a '-' line
            all_removed_old = {
                c["old_lineno"] for c in changes if c["raw"].startswith("-") and c["old_lineno"] is not None
            }
            method_set = set(range(start_ln, end_ln + 1))
            if method_set.issubset(all_removed_old):
                removed_method_ranges[key] = (start_ln, end_ln)
                script_logger.debug(f"Marked method {key} as completely_removed range {start_ln}-{end_ln}")

        # Step 3: Identify complete additions
        added_method_ranges = {}  # key → (start,end) in NEW version
        for new_ln, sig_str in plus_sigs:
            # If there is no existing method context at new_ln (i.e. parent is None),
            # but context_builder can’t see new‐only methods. So we instead search in old contexts
            # to see if a method existed there. If not, we consider a candidate for complete_add.
            parent = find_deepest_context(new_ln)
            if parent and parent.type == "method":
                # If parent was an existing method, it is not "completely_added" logic.
                continue
            # We need to scan contexts by signature text to find the correct (start,end) for this new method
            # We’ll find *any* context whose signature matches sig_str. There should be exactly one.
            candidates = [c for c in contexts if c.type == "method" and sig_str in c.signature]
            if not candidates:
                continue
            method_ctx = candidates[0]
            class_name = method_ctx.parent.name if method_ctx.parent else ""
            method_name = method_ctx.name
            key = (class_name, "method", method_name)
            added_method_ranges[key] = (method_ctx.start_line, method_ctx.end_line)
            script_logger.debug(f"Marked method {key} as completely_added range {method_ctx.start_line}-{method_ctx.end_line}")

        # Step 4: Now walk each changed line and dispatch
        for ch in changes:
            old_ln = ch["old_lineno"]
            new_ln = ch["new_lineno"]
            raw = ch["raw"]
            prefix = raw[0]
            content = raw[1:].rstrip()
            stripped = content.strip()
            script_logger.debug(f"Processing change line: {raw}")

            # 4.1  Check if this line belongs to a completely_removed method
            if old_ln is not None:
                for mkey, (s, e) in removed_method_ranges.items():
                    if s <= old_ln <= e and raw.startswith("-"):
                        entry = grouped.setdefault(
                            mkey,
                            {
                                "diff_lines": [],
                                "context": None,
                                "remove_sig": True,
                                "add_sig": False,
                                "body_changes": [],
                                "change_types": set(),
                            },
                        )
                        entry["diff_lines"].append(raw)
                        # Attach context (use a dummy _Context that spans start→end)
                        entry["context"] = _Context("method", mkey[2], s, e, sig_str, None)
                        entry["computed_change_type"] = "completely_removed"
                        script_logger.debug(f"Assigned {raw} to completely_removed {mkey}")
                        break
                else:
                    # Not in a fully removed method, fall through
                    pass
                # If it was assigned, skip further checks
                if any(s <= old_ln <= e for s, e in removed_method_ranges.values()):
                    continue

            # 4.2  Check if this line belongs to a completely_added method
            if new_ln is not None:
                for mkey, (s, e) in added_method_ranges.items():
                    if s <= new_ln <= e and raw.startswith("+"):
                        entry = grouped.setdefault(
                            mkey,
                            {
                                "diff_lines": [],
                                "context": None,
                                "remove_sig": False,
                                "add_sig": True,
                                "body_changes": [],
                                "change_types": set(),
                            },
                        )
                        entry["diff_lines"].append(raw)
                        entry["context"] = _Context("method", mkey[2], s, e, sig_str, None)
                        entry["computed_change_type"] = "completely_added"
                        script_logger.debug(f"Assigned {raw} to completely_added {mkey}")
                        break
                else:
                    pass
                if any(s <= new_ln <= e for s, e in added_method_ranges.values()):
                    continue

            # 4.3  IMPORT
            if stripped.startswith("import "):
                key = ("", "import", stripped)
                entry = grouped.setdefault(
                    key,
                    {
                        "diff_lines": [],
                        "context": None,
                        "remove_sig": False,
                        "add_sig": False,
                        "body_changes": [],
                        "change_types": set(),
                    },
                )
                entry["diff_lines"].append(raw)
                if prefix == "+":
                    entry["add_sig"] = True
                if prefix == "-":
                    entry["remove_sig"] = True
                continue

            # 4.4  MEMBER VARIABLE
            if MethodExtractor.is_member_variable(stripped):
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ("class", "enum"):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ("class", "enum") else ""
                var_name = MethodExtractor.extract_variable_name(stripped) or "N/A"
                key = (class_name, "member_variable", var_name)
                entry = grouped.setdefault(
                    key,
                    {
                        "diff_lines": [],
                        "context": None,
                        "remove_sig": False,
                        "add_sig": False,
                        "body_changes": [],
                        "change_types": set(),
                    },
                )
                entry["diff_lines"].append(raw)
                if prefix == "-":
                    entry["remove_sig"] = True
                    if lookup_ln:
                        field_ctx = find_deepest_context(lookup_ln)
                        if field_ctx and field_ctx.type == "member_variable" and field_ctx.name == var_name:
                            entry["context"] = field_ctx
                if prefix == "+":
                    entry["add_sig"] = True
                continue

            # 4.5  PARTIAL METHOD SIGNATURE or BODY
            # If it looks like a method signature, treat as “modified” signature
            if MethodExtractor.is_function_line(stripped) and "(" in stripped:
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ("class", "enum"):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ("class", "enum") else ""
                method_name = MethodExtractor.extract_method_name(stripped) or "N/A"
                key = (class_name, "method", method_name)
                entry = grouped.setdefault(
                    key,
                    {
                        "diff_lines": [],
                        "context": None,
                        "remove_sig": False,
                        "add_sig": False,
                        "body_changes": [],
                        "change_types": set(),
                    },
                )
                entry["diff_lines"].append(raw)
                if prefix == "-":
                    entry["remove_sig"] = True
                if prefix == "+":
                    entry["add_sig"] = True
                if lookup_ln:
                    method_ctx = find_deepest_context(lookup_ln)
                    if method_ctx and method_ctx.type == "method" and method_ctx.name == method_name:
                        entry["context"] = method_ctx
                continue

            # 4.6  BODY CHANGE INSIDE AN EXISTING METHOD
            lookup_ln = old_ln if old_ln is not None else new_ln
            ctx = find_deepest_context(lookup_ln) if lookup_ln else None
            if not ctx:
                script_logger.debug("No context found for body change line")
                continue
            c = ctx
            while c and c.type != "method":
                c = c.parent
            if c and c.type == "method":
                class_parent = c.parent
                while class_parent and class_parent.type not in ("class", "enum"):
                    class_parent = class_parent.parent
                class_name = class_parent.name if class_parent and class_parent.type in ("class", "enum") else ""
                key = (class_name, "method", c.name)
                entry = grouped.setdefault(
                    key,
                    {
                        "diff_lines": [],
                        "context": c,
                        "remove_sig": False,
                        "add_sig": False,
                        "body_changes": [],
                        "change_types": set(),
                    },
                )
                entry["diff_lines"].append(raw)
                entry["body_changes"].append(lookup_ln)
                continue

            # If we reach here, we couldn’t classify the line:
            script_logger.debug(f"No matching element for diff line: {raw}")

        # Step 5: Compute final change_type
        script_logger.debug("Computing final change types for grouped elements")
        for key, info in grouped.items():
            elem_type = key[1]
            if elem_type == "method":
                if info.get("computed_change_type") == "completely_removed":
                    continue
                if key in added_method_ranges and info["add_sig"] and not info["remove_sig"]:
                    info["computed_change_type"] = "completely_added"
                elif key in removed_method_ranges and info["remove_sig"] and not info["add_sig"]:
                    info["computed_change_type"] = "completely_removed"
                else:
                    info["computed_change_type"] = "modified"
            elif elem_type == "import":
                adds = info["add_sig"]
                removes = info["remove_sig"]
                if adds and not removes:
                    info["computed_change_type"] = "added"
                elif removes and not adds:
                    info["computed_change_type"] = "removed"
                else:
                    info["computed_change_type"] = "modified"
            else:
                # class or member_variable
                adds = any(line.startswith("+") for line in info["diff_lines"])
                removes = any(line.startswith("-") for line in info["diff_lines"])
                if adds and not removes:
                    info["computed_change_type"] = "added"
                elif removes and not adds:
                    info["computed_change_type"] = "removed"
                else:
                    info["computed_change_type"] = "modified"

        script_logger.debug(f"_group_changes_for_file complete for {file_path}")
        return grouped
