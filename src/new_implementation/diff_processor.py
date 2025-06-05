import os
import re
import logging
import pandas as pd
from typing import List, Dict

from java_context_extractor import JavaContextExtractor

script_logger = logging.getLogger("diff_processor")
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

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
        Groups changes by both high-level class and low-level element (method/member_variable).
        Fully removed methods (all consecutive '-' lines covering the method span)
        are recorded first. Remaining changes get assigned to contexts and classified.
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

        # Phase 2: for each unique .java file...
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

            # Subset of changes for this file
            file_changes = changed_df[changed_df["file_path"] == fp].copy()

            # ---------- FIRST: Detect entire-method removals ----------
            # Filter only removed lines and sort by line_number
            removed_only = file_changes[file_changes["change_type"] == "removed"]
            used_indices = set()
            if not removed_only.empty:
                removed_sorted = removed_only.sort_values("line_number")
                removed_rows = removed_sorted.to_dict("records")
                runs: List[List[Dict]] = []
                current_run: List[Dict] = []

                prev_ln = None
                for row in removed_rows:
                    ln = row["line_number"]
                    if prev_ln is None or ln == prev_ln + 1:
                        current_run.append(row)
                    else:
                        runs.append(current_run)
                        current_run = [row]
                    prev_ln = ln
                if current_run:
                    runs.append(current_run)

                # For each run of consecutive '-' lines, check against method contexts
                for run in runs:
                    run_start = run[0]["line_number"]
                    run_end = run[-1]["line_number"]
                    run_len = run_end - run_start + 1

                    # Find a method context whose span exactly matches
                    for idx_ctx, ctx in enumerate(contexts):
                        if ctx["type"] == "method" \
                           and ctx["start_line"] == run_start \
                           and ctx["end_line"] == run_end:
                            # Entire method removed
                            # Find enclosing class for class_name
                            class_name = None
                            for parent_ctx in contexts:
                                if parent_ctx["type"] == "class" \
                                   and parent_ctx["start_line"] <= run_start <= parent_ctx["end_line"]:
                                    class_name = parent_ctx["element_name"]
                                    break

                            all_records.append({
                                "file_path": fp,
                                "class_name": class_name,
                                "element_type": "method",
                                "element_name": ctx["element_name"],
                                "change_type": "removed",
                                "diff_lines": ["-" + r["raw_text"] for r in run],
                                "element_source": None
                            })
                            # Mark these rows as used
                            for r in run:
                                used_indices.add(r["line_number"])
                            break

            # Remove used rows from file_changes
            file_changes = file_changes[~file_changes["line_number"].isin(used_indices)]

            # ---------- NEXT: Assign remaining changes to contexts ----------
            # Map each context index to its list of remaining change rows
            context_changes: Dict[int, List[pd.Series]] = {i: [] for i in range(len(contexts))}

            for _, change_row in file_changes.iterrows():
                ln = change_row["line_number"]
                # Find all contexts containing this line
                matching_indices = [
                    idx_ctx for idx_ctx, ctx in enumerate(contexts)
                    if ctx["start_line"] <= ln <= ctx["end_line"]
                ]
                if not matching_indices:
                    continue

                # Choose smallest-span context
                best_idx = min(
                    matching_indices,
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

                added_lines = [chg for chg in change_list if chg["change_type"] == "added"]
                removed_lines = [chg for chg in change_list if chg["change_type"] == "removed"]

                removed_line_numbers = {chg["line_number"] for chg in removed_lines}
                added_line_numbers = {chg["line_number"] for chg in added_lines}

                # Determine if entire element added/removed
                if len(removed_line_numbers) == span_length and not added_lines:
                    overall_change_type = "removed"
                elif len(added_line_numbers) == span_length and not removed_lines:
                    overall_change_type = "added"
                else:
                    overall_change_type = "modified"

                diff_lines = [chg["diff_line"] for chg in change_list]

                # Determine class_name
                class_name = None
                if element_type == "class":
                    class_name = element_name
                else:
                    for parent_ctx in contexts:
                        if parent_ctx["type"] == "class" \
                           and parent_ctx["start_line"] <= start_line <= parent_ctx["end_line"]:
                            class_name = parent_ctx["element_name"]
                            break

                # Extract element_source
                if overall_change_type == "removed":
                    element_source = None
                else:
                    snippet = "".join(full_source_lines[start_line - 1 : end_line]) if full_source_lines else None
                    element_source = snippet

                all_records.append({
                    "file_path": fp,
                    "class_name": class_name,
                    "element_type": element_type,
                    "element_name": element_name,
                    "change_type": overall_change_type,
                    "diff_lines": diff_lines,
                    "element_source": element_source
                })

        # Return as DataFrame
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
