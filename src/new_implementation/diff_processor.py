import os
import re
import logging
import pandas as pd

from method_extractor import extract_java_context

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
        # Phase 1: collect changed lines
        changed_df = self._collect_changed_lines(diff_text)

        # Phase 2: filter to .java files and build contexts
        file_to_contexts = {}
        unique_files = changed_df['file_path'].unique()
        for fp in unique_files:
            if not fp.endswith('.java'):
                script_logger.debug(f"Skipping non-Java file: {fp}")
                continue
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = extract_java_context(full_disk)

        return file_to_contexts

        # Phase 3: group changes per file by element
        # TODO

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
