import re
import logging
import pandas as pd
from collections import defaultdict

script_logger = logging.getLogger("diff_processor")
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

class DiffProcessor:
    """
    Parses a unified diff and outputs a DataFrame listing each changed line with:
      - file_path
      - change_type ('added' or 'removed')
      - line_number (new line for additions, old line for removals)
      - raw_text (including '+' or '-')

    Ignores diff metadata (diff --git, index, etc.) and handles "\ No newline at end of file".
    Logs entering each file and each added/removed line.
    """

    def __init__(self, repo_root: str = None):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        per_file_changes = []
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        # Regex to match hunk headers like: @@ -12,7 +12,8 @@
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
            # File header for new file path
            if raw.startswith('+++ '):
                path = raw[4:].strip()
                if path != '/dev/null':
                    current_file = path
                    script_logger.debug(f"Entering file: {current_file}")
                else:
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
                change = {
                    'file_path': current_file,
                    'change_type': 'removed',
                    'line_number': running_old,
                    'raw_text': raw
                }
                per_file_changes.append(change)
                script_logger.debug(f"Removed line {running_old}: {raw}")
                running_old += 1
                continue
            # Added line
            if raw.startswith('+'):
                change = {
                    'file_path': current_file,
                    'change_type': 'added',
                    'line_number': running_new,
                    'raw_text': raw
                }
                per_file_changes.append(change)
                script_logger.debug(f"Added line {running_new}: {raw}")
                running_new += 1
                continue
            # Anything else (should not happen inside hunk)
            script_logger.debug(f"Unexpected line in hunk: {raw}")

        df = pd.DataFrame(per_file_changes, columns=['file_path', 'change_type', 'line_number', 'raw_text'])
        return df
