import os
import re
import logging
import pandas as pd
from collections import defaultdict

from context_builder import build_contexts_for_file, _Context
from method_extractor import MethodExtractor

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
            file_to_contexts[fp] = build_contexts_for_file(full_disk)

        # Phase 3: group changes per file by element
        rows = []
        for fp in unique_files:
            if not fp.endswith('.java'):
                continue
            file_changes = changed_df[changed_df['file_path'] == fp]
            contexts = file_to_contexts.get(fp, [])
            grouped = self._group_changes_for_file(fp, file_changes, contexts)

            # flatten grouped into rows
            for (class_name, elem_type, elem_name), info in grouped.items():
                rows.append({
                    'file_path': fp,
                    'class_name': class_name,
                    'element_type': elem_type,
                    'element_name': elem_name,
                    'change_type': info['change_type'],
                    'diff_lines': info['diff_lines'],
                    'element_source': info.get('element_source', '')
                })

        final_df = pd.DataFrame(rows, columns=[
            'file_path', 'class_name', 'element_type', 'element_name', 'change_type', 'diff_lines', 'element_source'
        ])
        return final_df

    def _collect_changed_lines(self, diff_text: str) -> pd.DataFrame:
        records = []
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        hunk_re = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

        for raw in diff_text.splitlines():
            if raw.startswith('diff --git '):
                in_hunk = False
                continue
            if raw.startswith('index '):
                continue
            if raw.startswith('\\ No newline at end of file'):
                continue
            if raw.startswith('+++ '):
                path = raw[4:].strip()
                if path.endswith('.java') and path != '/dev/null':
                    current_file = path
                    script_logger.debug(f"Entering file: {current_file}")
                else:
                    script_logger.debug(f"Skipping non-Java or deleted file: {path}")
                    current_file = None
                in_hunk = False
                continue
            if raw.startswith('--- '):
                continue

            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1))
                running_new = int(m.group(2))
                in_hunk = True
                script_logger.debug(f"Start hunk in {current_file}: old={running_old}, new={running_new}")
                continue
            if not in_hunk or not current_file:
                continue
            if raw.startswith(' '):
                running_old += 1
                running_new += 1
                continue
            if raw.startswith('-'):
                records.append({
                    'file_path': current_file,
                    'change_type': 'removed',
                    'line_number': running_old,
                    'raw_text': raw
                })
                script_logger.debug(f"Removed line {running_old}: {raw}")
                running_old += 1
                continue
            if raw.startswith('+'):
                records.append({
                    'file_path': current_file,
                    'change_type': 'added',
                    'line_number': running_new,
                    'raw_text': raw
                })
                script_logger.debug(f"Added line {running_new}: {raw}")
                running_new += 1
                continue
            script_logger.debug(f"Unexpected line in hunk: {raw}")

        return pd.DataFrame(records, columns=['file_path','change_type','line_number','raw_text'])

    def _group_changes_for_file(self, file_path, file_changes, contexts):
        # Sort by line_number
        file_changes = file_changes.sort_values('line_number')

        # Prepare result dict
        grouped = {}

        # Build a set of removed line_numbers for quick lookup
        removed_set = set(file_changes[file_changes['change_type']=='removed']['line_number'])

        # 1) Detect completely removed methods by scanning for runs of '-' that form a balanced brace block matching a method
        for idx, row in file_changes[file_changes['change_type']=='removed'].iterrows():
            ln = row['line_number']
            raw = row['raw_text']
            stripped = raw[1:].strip()
            if MethodExtractor.is_function_line(stripped) and '(' in stripped:
                # Potential removed signature
                script_logger.debug(f"Found removed signature candidate at {ln}: {stripped}")
                # Extract context to know method boundaries
                mctx = next((c for c in contexts if c.type=='method' and c.start_line==ln), None)
                if not mctx:
                    continue
                start, end = mctx.start_line, mctx.end_line
                # Check if all lines in [start..end] are in removed_set
                if set(range(start, end+1)).issubset(removed_set):
                    key = (mctx.parent.name if mctx.parent else '', 'method', mctx.name)
                    entry = grouped.setdefault(key, {'diff_lines':[], 'change_type':'removed', 'element_source':None})
                    # Collect all those lines' raw_text
                    for o in range(start, end+1):
                        raw_line = file_changes[(file_changes['line_number']==o) & (file_changes['change_type']=='removed')]['raw_text']
                        if not raw_line.empty:
                            entry['diff_lines'].append(raw_line.values[0])
                    script_logger.debug(f"Method {key} completely removed, lines {start}-{end}")

        # 2) Handle all other lines
        for idx, row in file_changes.iterrows():
            ln = row['line_number']
            raw = row['raw_text']
            ctype = row['change_type']

            # Skip lines already grouped into completely removed methods
            in_removed = False
            for key, info in grouped.items():
                if key[1]=='method' and info['change_type']=='removed':
                    # find that method's context
                    mctx = next((c for c in contexts if c.type=='method' and c.name==key[2] and (c.parent.name if c.parent else '')==key[0]), None)
                    if mctx and mctx.start_line <= ln <= mctx.end_line:
                        in_removed = True
                        break
            if in_removed:
                continue

            # IMPORT
            stripped = raw[1:].strip()
            if stripped.startswith('import '):
                key = ('', 'import', stripped)
                entry = grouped.setdefault(key, {'diff_lines':[], 'change_type':None, 'element_source':None})
                entry['diff_lines'].append(raw)
                entry['change_type'] = 'added' if ctype=='added' else 'removed'
                continue

            # MEMBER VARIABLE
            if MethodExtractor.is_member_variable(stripped):
                # Find class context
                ctx = next((c for c in contexts if c.type=='member_variable' and c.start_line==ln), None)
                if not ctx:
                    # find by name and approximate location
                    name = MethodExtractor.extract_variable_name(stripped) or 'N/A'
                    ctx = next((c for c in contexts if c.type=='member_variable' and c.name==name and c.start_line<=ln<=c.end_line), None)
                class_name = ctx.parent.name if ctx and ctx.parent else ''
                key = (class_name, 'member_variable', ctx.name if ctx else stripped)
                entry = grouped.setdefault(key, {'diff_lines':[], 'change_type':None, 'element_source':None})
                entry['diff_lines'].append(raw)
                entry['change_type'] = 'added' if ctype=='added' else 'removed'
                if ctype=='added' and ctx:
                    # extract source line from disk
                    entry['element_source'] = ctx.signature
                continue

            # METHOD SIGNATURE ADD or BODY CHANGE
            if ctype=='added' and MethodExtractor.is_function_line(stripped) and '(' in stripped:
                lookup_ln = ln
                mctx = next((c for c in contexts if c.type=='method' and c.start_line==lookup_ln), None)
                if not mctx:
                    # approximate by name match
                    mname = MethodExtractor.extract_method_name(stripped)
                    mctx = next((c for c in contexts if c.type=='method' and c.name==mname and c.start_line<=lookup_ln<=c.end_line), None)
                class_name = mctx.parent.name if mctx and mctx.parent else ''
                key = (class_name, 'method', mctx.name if mctx else stripped)
                entry = grouped.setdefault(key, {'diff_lines':[], 'change_type':None, 'element_source':None})
                entry['diff_lines'].append(raw)
                entry['change_type'] = 'added'
                if mctx:
                    entry['element_source'] = '\n'.join(self._read_file_segment(file_path, mctx.start_line, mctx.end_line))
                continue

            # BODY CHANGE INSIDE REMAINING METHOD (removed inside)
            if ctype=='removed':
                ctx = next((c for c in contexts if c.type=='method' and c.start_line<ln< c.end_line), None)
                if ctx:
                    class_name = ctx.parent.name if ctx.parent else ''
                    key = (class_name, 'method', ctx.name)
                    entry = grouped.setdefault(key, {'diff_lines':[], 'change_type':'removed', 'element_source':None})
                    entry['diff_lines'].append(raw)
                    continue

            # Anything else skip
            script_logger.debug(f"Skipping line not matched to element: {raw}")

        return grouped

    def _read_file_segment(self, file_path, start, end):
        try:
            with open(os.path.join(self.repo_root, file_path), 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
                return lines[start-1:end]
        except Exception:
            return []
