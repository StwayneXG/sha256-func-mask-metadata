import os
import re
import logging
import pandas as pd
from typing import List, Dict

from java_context_extractor import JavaContextExtractor

script_logger = logging.getLogger("diff_processor")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

class DiffProcessor:
    """
    Phase 1: _collect_changed_lines → DataFrame of {file_path, change_type, line_number, raw_text}
    Phase 2: build contexts for each .java file
    Phase 3: group changes by element (method, class, enum, member_variable, import)
      - detect completely removed methods by scanning consecutive '-' lines and matching braces
      - group other changes by looking up context for added lines and removed-inside-body lines
    """

    def __init__(self, repo_root: str = None):
        self.repo_root = repo_root


    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        """
        Groups changes by both high-level class and low-level element (method/member_variable/import).
        Fully removed methods are detected by inspecting consecutive '-' runs.
        Anything not matched as a full-method removal is assigned to contexts and classified.
        Adds a separate row for the enclosing class (element_type="class") with its full source
        and a list of all changed lines inside that class.
        """
        # Phase 1: collect changed lines
        changed_df = self._collect_changed_lines(diff_text).copy()
        # Reconstruct each diff line with its prefix
        changed_df["diff_line"] = changed_df.apply(
            lambda row: ("+" + row["raw_text"]
                         if row["change_type"] == "added"
                         else "-" + row["raw_text"]),
            axis=1
        )

        all_records: List[Dict] = []

        # Phase 2: process each Java file separately
        for fp in changed_df["file_path"].unique():
            if not fp.endswith(".java"):
                continue

            full_disk = os.path.join(self.repo_root, fp)
            try:
                contexts = JavaContextExtractor.extract_context(full_disk)
                full_source_lines = open(full_disk, "r", encoding="utf-8").readlines()
            except FileNotFoundError:
                contexts = []
                full_source_lines = []

            # Build a quick map from each class context to its source lines
            class_contexts = [ctx for ctx in contexts if ctx["type"] == "class"]
            class_to_source: Dict[str, str] = {}
            for cctx in class_contexts:
                c_start, c_end = cctx["start_line"], cctx["end_line"]
                class_to_source[cctx["element_name"]] = "".join(
                    full_source_lines[c_start - 1:c_end]
                )

            # Subset of changes for this file
            file_changes = changed_df[changed_df["file_path"] == fp].copy()

            # ---------- Detect full-method removals by inspecting runs of consecutive '-' ----------
            removed_only = file_changes[file_changes["change_type"] == "removed"].sort_values("line_number")
            used_indices = set()

            if not removed_only.empty:
                rows = removed_only.to_dict("records")
                runs: List[List[Dict]] = []
                current_run: List[Dict] = []
                prev_ln = None

                for row in rows:
                    ln = row["line_number"]
                    if prev_ln is None or ln == prev_ln + 1:
                        current_run.append(row)
                    else:
                        runs.append(current_run)
                        current_run = [row]
                    prev_ln = ln
                if current_run:
                    runs.append(current_run)

                # For each run of consecutive '-' lines, see if it covers exactly one method
                for run in runs:
                    # Attempt to find a signature line anywhere in the run
                    sig_idx = None
                    for i, r in enumerate(run):
                        text = r["raw_text"].strip()
                        if JavaContextExtractor._is_function_line(text):
                            sig_idx = i
                            break
                    if sig_idx is None:
                        continue

                    run_start = run[0]["line_number"]
                    run_end = run[-1]["line_number"]
                    run_len = run_end - run_start + 1

                    # Now count braces from sig_idx onward (within the run) to find body end
                    brace_balance = 0
                    found_start_brace = False
                    local_end_idx = None

                    for i in range(sig_idx, len(run)):
                        t = run[i]["raw_text"]
                        if "//" in t:
                            t = t.split("//", 1)[0]
                        while "/*" in t and "*/" in t:
                            t = t[:t.find("/*")] + t[t.find("*/") + 2:]
                        for ch in t:
                            if ch == "{":
                                brace_balance += 1
                                found_start_brace = True
                            elif ch == "}":
                                brace_balance -= 1
                                if found_start_brace and brace_balance == 0:
                                    local_end_idx = i
                                    break
                        if local_end_idx is not None:
                            break

                    # If the body-end is exactly the last line of the run, this run is a full-method removal
                    if local_end_idx is not None and (local_end_idx + 1) == run_len:
                        sig_text = run[sig_idx]["raw_text"].strip()
                        method_name = JavaContextExtractor._extract_method_name(sig_text)

                        # Find enclosing class for class_name
                        class_name = None
                        for parent_ctx in class_contexts:
                            if parent_ctx["start_line"] <= run_start <= parent_ctx["end_line"]:
                                class_name = parent_ctx["element_name"]
                                break

                        all_records.append({
                            "file_path": fp,
                            "class_name": class_name,
                            "element_type": "method",
                            "element_name": method_name,
                            "change_type": "removed",
                            "diff_lines": ["-" + r["raw_text"] for r in run],
                            "element_source": None
                        })

                        # Mark these line numbers as used
                        for r in run:
                            used_indices.add(r["line_number"])

            # Remove those used rows before further assignment
            file_changes = file_changes[~file_changes["line_number"].isin(used_indices)]

            # ---------- Assign remaining changes to contexts ----------
            context_changes: Dict[int, List[pd.Series]] = {i: [] for i in range(len(contexts))}
            for _, change_row in file_changes.iterrows():
                ln = change_row["line_number"]
                matching = [
                    idx_ctx for idx_ctx, ctx in enumerate(contexts)
                    if ctx["start_line"] <= ln <= ctx["end_line"]
                ]
                if not matching:
                    continue
                best_idx = min(
                    matching,
                    key=lambda i: (contexts[i]["end_line"] - contexts[i]["start_line"])
                )
                context_changes[best_idx].append(change_row)

            # Build one record per context with remaining changes
            for idx_ctx, change_list in context_changes.items():
                if not change_list:
                    continue

                ctx = contexts[idx_ctx]
                element_type = ctx["type"]
                element_name = ctx["element_name"]
                start_line = ctx["start_line"]
                end_line = ctx["end_line"]
                span_length = end_line - start_line + 1

                added = [chg for chg in change_list if chg["change_type"] == "added"]
                removed = [chg for chg in change_list if chg["change_type"] == "removed"]

                removed_lns = {chg["line_number"] for chg in removed}
                added_lns = {chg["line_number"] for chg in added}

                if len(removed_lns) == span_length and not added:
                    overall_type = "removed"
                elif len(added_lns) == span_length and not removed:
                    overall_type = "added"
                else:
                    overall_type = "modified"

                diff_lines = [chg["diff_line"] for chg in change_list]

                # Determine class_name for this element
                class_name = None
                for parent_ctx in class_contexts:
                    if parent_ctx["start_line"] <= start_line <= parent_ctx["end_line"]:
                        class_name = parent_ctx["element_name"]
                        break

                if overall_type == "removed":
                    element_source = None
                else:
                    snippet = "".join(full_source_lines[start_line - 1 : end_line]) if full_source_lines else None
                    element_source = snippet

                all_records.append({
                    "file_path": fp,
                    "class_name": class_name,
                    "element_type": element_type,
                    "element_name": element_name,
                    "change_type": overall_type,
                    "diff_lines": diff_lines,
                    "element_source": element_source
                })

            # ---------- Finally, add one row per class context if it had any changes  ----------
            file_changed_lns = set(changed_df[changed_df["file_path"] == fp]["line_number"])
            for cctx in class_contexts:
                c_start, c_end = cctx["start_line"], cctx["end_line"]
                class_span = c_end - c_start + 1

                changed_in_class = {ln for ln in file_changed_lns if c_start <= ln <= c_end}
                if not changed_in_class:
                    continue

                removed_in_class = {ln for ln in changed_df[
                    (changed_df["file_path"] == fp) & 
                    (changed_df["change_type"] == "removed")
                ]["line_number"] if c_start <= ln <= c_end}

                # Collect all diff_lines inside this class span
                df_file = changed_df[changed_df["file_path"] == fp]
                diff_lines_class = df_file[
                    (df_file["line_number"] >= c_start) & (df_file["line_number"] <= c_end)
                ]["diff_line"].tolist()

                if len(removed_in_class) == class_span:
                    class_change_type = "removed"
                    class_source = None
                else:
                    class_change_type = "modified"
                    class_source = class_to_source.get(cctx["element_name"])

                all_records.append({
                    "file_path": fp,
                    "class_name": None,
                    "element_type": "class",
                    "element_name": cctx["element_name"],
                    "change_type": class_change_type,
                    "diff_lines": diff_lines_class,
                    "element_source": class_source
                })

        return pd.DataFrame(all_records, columns=[
            "file_path",
            "class_name",
            "element_type",
            "element_name",
            "change_type",
            "diff_lines",
            "element_source"
        ])

    def _collect_changed_lines(self, diff_text: str) -> pd.DataFrame:
        records = []
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        # Unified-diff hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        hunk_re = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

        for raw in diff_text.splitlines():
            # Skip high-level diff markers
            if raw.startswith("diff --git "):
                in_hunk = False
                continue
            if raw.startswith("index "):
                continue
            if raw.startswith("\\ No newline at end of file"):
                continue

            # Detect the “+++” line: indicates the new file path
            if raw.startswith("+++ "):
                raw_path = raw[4:].strip()  # e.g. "b/src/com/example/MyClass.java" or "/dev/null"
                if raw_path.endswith(".java") and raw_path != "/dev/null":
                    # Drop leading "a/" or "b/" so file paths are just "src/com/..."
                    if raw_path.startswith(("a/", "b/")):
                        raw_path = raw_path[2:]
                    current_file = raw_path
                    script_logger.debug(f"Entering file: {current_file}")
                else:
                    script_logger.debug(f"Skipping non-Java or deleted file: {raw_path}")
                    current_file = None
                in_hunk = False
                continue

            # We don’t need the “---” line for our current logic
            if raw.startswith("--- "):
                continue

            # If we see a hunk header and we’re in a valid Java file, start counting
            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1))
                running_new = int(m.group(2))
                in_hunk = True
                script_logger.debug(f"Start hunk in {current_file}: old={running_old}, new={running_new}")
                continue

            # Ignore everything until we know we’re inside a hunk of a Java file
            if not in_hunk or not current_file:
                continue

            # Context line — just advance both counters
            if raw.startswith(" "):
                running_old += 1
                running_new += 1
                continue

            # Removed line
            if raw.startswith("-"):
                content = raw[1:]  # drop the leading “-”
                records.append({
                    "file_path": current_file,
                    "change_type": "removed",
                    "line_number": running_old,
                    "raw_text": content,
                })
                script_logger.debug(f"Removed line {running_old}: {content}")
                running_old += 1
                continue

            # Added line
            if raw.startswith("+"):
                content = raw[1:]  # drop the leading “+”
                records.append({
                    "file_path": current_file,
                    "change_type": "added",
                    "line_number": running_new,
                    "raw_text": content,
                })
                script_logger.debug(f"Added line {running_new}: {content}")
                running_new += 1
                continue

            # If we get here, something strange is inside the hunk
            script_logger.debug(f"Unexpected line in hunk: {raw}")

        return pd.DataFrame(records, columns=["file_path", "change_type", "line_number", "raw_text"])
