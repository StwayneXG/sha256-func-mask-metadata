import os
import re
import logging
import pandas as pd
from collections import defaultdict

from context_builder import build_contexts_for_file, _Context

script_logger = logging.getLogger("diff_processor")
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

class DiffProcessor:
    """
    Step 1: Only parse changed lines for .java files.
    Step 2: Build contexts for those files.
    Step 3: Group changed lines by methods.

    For now, we listen for:
      - file_path (ends with .java)
      - change_type (added/removed)
      - line_number (old or new line index)
      - raw_text (the +/- line)

    Later, we will group by methods (detect complete removals by brace balance).
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        # Step 1: collect raw changed lines (_collect_changed_lines)
        per_file_raw = self._collect_changed_lines(diff_text)

        # Step 2: build contexts only for .java files that had changes
        file_contexts = {}
        for fp in per_file_raw.keys():
            full_path = os.path.join(self.repo_root, fp)
            file_contexts[fp] = build_contexts_for_file(full_path)

        # Step 3: group by methods (to implement next)
        rows = []
        for fp, changes in per_file_raw.items():
            contexts = file_contexts.get(fp, [])
            grouped = self._group_by_method(fp, changes, contexts)
            rows.extend(grouped)

        df = pd.DataFrame(rows, columns=[
            'file_path', 'class_name', 'element_type', 'element_name',
            'change_type', 'diff_lines', 'element_source'
        ])
        return df

    def _collect_changed_lines(self, diff_text: str):
        script_logger.debug("Starting _collect_changed_lines")
        per_file = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        hunk_re = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

        for raw in diff_text.splitlines():
            # Skip diff metadata
            if raw.startswith('diff --git '):
                in_hunk = False
                continue
            if raw.startswith('index '):
                continue
            if raw.startswith('\\ No newline at end of file'):
                continue
            # New file header
            if raw.startswith('+++ '):
                path = raw[4:].strip()
                if path.endswith('.java') and path != '/dev/null':
                    current_file = path
                    per_file[current_file] = []
                    script_logger.debug(f"Entering file: {current_file}")
                else:
                    script_logger.debug(f"Skipping non-Java file: {path}")
                    current_file = None
                in_hunk = False
                continue
            if raw.startswith('--- '):
                continue

            # Hunk header
            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1))
                running_new = int(m.group(2))
                in_hunk = True
                script_logger.debug(f"Start hunk in {current_file}: old={running_old}, new={running_new}")
                continue
            if not in_hunk or not current_file:
                continue
            # Context line
            if raw.startswith(' '):
                running_old += 1
                running_new += 1
                continue
            # Removed line
            if raw.startswith('-'):
                per_file[current_file].append({
                    'change_type': 'removed',
                    'line_number': running_old,
                    'raw_text': raw
                })
                script_logger.debug(f"Removed line {running_old}: {raw}")
                running_old += 1
                continue
            # Added line
            if raw.startswith('+'):
                per_file[current_file].append({
                    'change_type': 'added',
                    'line_number': running_new,
                    'raw_text': raw
                })
                script_logger.debug(f"Added line {running_new}: {raw}")
                running_new += 1
                continue
            # Unexpected
            script_logger.debug(f"Unexpected line in hunk: {raw}")

        script_logger.debug("_collect_changed_lines complete")
        return per_file

    def _group_by_method(self, file_path, changes, contexts):
        """
        Placeholder: group changed lines by method.  For now, simply output
        each change as a separate row with minimal info.

        Returns list of dicts with keys:
          - file_path
          - class_name  (empty for now)
          - element_type ('method' or 'member_variable' or 'import')
          - element_name (raw signature or field name)
          - change_type ('added'/'removed')
          - diff_lines ([raw_text])
          - element_source (None)
        """
        rows = []
        for ch in sorted(changes, key=lambda x: x['line_number']):
            rows.append({
                'file_path': file_path,
                'class_name': '',
                'element_type': 'method',  # placeholder
                'element_name': '',       # placeholder
                'change_type': ch['change_type'],
                'diff_lines': [ch['raw_text']],
                'element_source': None
            })
        return rows
