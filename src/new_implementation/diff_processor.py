import os
import re
import logging
import pandas as pd
from collections import defaultdict

from method_extractor import MethodExtractor
from context_builder import build_contexts_for_file, _Context

script_logger = logging.getLogger("diff_processor")

class DiffProcessor:
    """
    Parses a unified diff and produces a DataFrame of changed elements
    (class, enum, method, or member_variable).  Each element row includes
    the full source body of that element as 'element_source'.

    For methods, change_type becomes one of:
      - completely_added   (method signature + entire body added)
      - completely_removed (entire original method signature + body removed)
      - modified           (any partial changes inside a method)
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        """
        Main entry point.  Returns a pandas DataFrame with columns:
          - file_path
          - class_name
          - element_type    (class | enum | method | member_variable)
          - element_name
          - change_type     (added | removed | modified for non-methods; for methods: completely_added | completely_removed | modified)
          - diff_lines      (list of +/− lines)
          - element_source  (full source code for methods and member_variables, empty for others)
        """
        per_file_changes = self._collect_changed_lines(diff_text)

        # Build contexts from original files
        file_to_contexts = {}
        for fp in per_file_changes:
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = build_contexts_for_file(full_disk)

        # Group changes
        grouped = {}
        for fp, changes in per_file_changes.items():
            contexts = file_to_contexts.get(fp, [])
            grouped[fp] = self._group_changes_for_file(fp, changes, contexts)

        # Flatten to DataFrame
        records = []
        for fp, groups in grouped.items():
            full_disk = os.path.join(self.repo_root, fp)
            file_lines = []
            if os.path.isfile(full_disk):
                with open(full_disk, 'r', encoding='utf-8') as f:
                    file_lines = f.read().splitlines()
            else:
                script_logger.warning(f"Cannot open {full_disk} to extract element source.")

            for (class_name, elem_type, elem_name), info in groups.items():
                change_type = info.get('computed_change_type')
                # Skip methods completely removed
                if elem_type == 'method' and change_type == 'completely_removed':
                    continue

                # element_source extraction
                elem_src = ''
                if elem_type == 'method':
                    if change_type != 'completely_removed':
                        ctx = info.get('context')
                        if ctx and file_lines:
                            start, end = ctx.start_line, ctx.end_line
                            elem_src = "\n".join(file_lines[start-1:end])
                        else:
                            script_logger.debug(f"No context or file lines for method {elem_name} in {fp}.")
                elif elem_type == 'member_variable':
                    if change_type != 'removed':
                        ctx = info.get('context')
                        if ctx and file_lines:
                            idx = ctx.start_line
                            elem_src = file_lines[idx-1]
                        else:
                            script_logger.debug(f"No context or file lines for member_variable {elem_name} in {fp}.")

                records.append({
                    'file_path': fp,
                    'class_name': class_name,
                    'element_type': elem_type,
                    'element_name': elem_name,
                    'change_type': change_type,
                    'diff_lines': info['diff_lines'],
                    'element_source': elem_src
                })

        df = pd.DataFrame(records, columns=[
            'file_path', 'class_name', 'element_type',
            'element_name', 'change_type', 'diff_lines', 'element_source'
        ])
        return df

    def _collect_changed_lines(self, diff_text: str):
        """
        Parse unified diff into file_path → list of {old_lineno, new_lineno, raw}.
        Skip metadata and keep import changes.
        """
        per_file_changes = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        hunk_re = re.compile(r'^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@')

        for raw in diff_text.splitlines():
            if raw.startswith('diff --git '):
                current_file = None
                in_hunk = False
                continue
            if raw.startswith('index '):
                continue
            if raw.startswith('\\ No newline at end of file'):
                continue

            if raw.startswith('+++ '):
                path = raw[4:].strip()
                if path != '/dev/null':
                    current_file = path
                    per_file_changes[current_file] = []
                else:
                    script_logger.warning(f"Entire file deleted or /dev/null: {raw}")
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
                continue

            if not in_hunk or not current_file:
                continue

            if raw.startswith(' '):
                running_old += 1
                running_new += 1
                continue

            prefix = raw[0]
            if prefix == '-':
                per_file_changes[current_file].append({
                    'old_lineno': running_old,
                    'new_lineno': None,
                    'raw': raw
                })
                running_old += 1
            elif prefix == '+':
                per_file_changes[current_file].append({
                    'old_lineno': None,
                    'new_lineno': running_new,
                    'raw': raw
                })
                running_new += 1
            else:
                script_logger.debug(f"Unexpected line in hunk: {raw}")

        return per_file_changes

    def _group_changes_for_file(self, file_path, changes, contexts):
        """
        Group changed lines into elements and classify methods as fully added/removed/modified.
        """
        def find_deepest_context(line_no):
            candidates = [c for c in contexts if c.start_line <= line_no <= c.end_line]
            return max(candidates, key=lambda c: c.start_line) if candidates else None

        grouped = {}
        for item in changes:
            old_ln = item['old_lineno']
            new_ln = item['new_lineno']
            raw_line = item['raw']
            prefix = raw_line[0]
            content = raw_line[1:].rstrip()
            stripped = content.strip()

            # CLASS/ENUM
            if (stripped.startswith('class ') or ' class ' in stripped or
                stripped.startswith('enum ') or ' enum ' in stripped):
                name = MethodExtractor.extract_method_name(stripped) or 'N/A'
                key = (name, 'class', name)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': None,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                entry['diff_lines'].append(raw_line)
                entry['change_types'].add('modified')
                continue

            # METHOD SIGNATURE
            if MethodExtractor.is_function_line(stripped) and '(' in stripped:
                mname = MethodExtractor.extract_method_name(stripped) or 'N/A'
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ('class', 'enum'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class', 'enum') else ''
                key = (class_name, 'method', mname)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': None,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                entry['diff_lines'].append(raw_line)
                if prefix == '-':
                    entry['remove_sig'] = True
                    if lookup_ln:
                        method_ctx = find_deepest_context(lookup_ln)
                        if method_ctx and method_ctx.type == 'method' and method_ctx.name == mname:
                            entry['context'] = method_ctx
                elif prefix == '+':
                    entry['add_sig'] = True
                continue

            # MEMBER VARIABLE
            if MethodExtractor.is_member_variable(stripped):
                varname = MethodExtractor.extract_variable_name(stripped) or 'N/A'
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ('class', 'enum'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class', 'enum') else ''
                key = (class_name, 'member_variable', varname)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': None,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                entry['diff_lines'].append(raw_line)
                if prefix == '-':
                    entry['remove_sig'] = True
                    if lookup_ln:
                        field_ctx = find_deepest_context(lookup_ln)
                        if field_ctx and field_ctx.type == 'member_variable' and field_ctx.name == varname:
                            entry['context'] = field_ctx
                elif prefix == '+':
                    entry['add_sig'] = True
                continue

            # BODY CHANGE INSIDE METHOD
            lookup_ln = old_ln if old_ln is not None else new_ln
            ctx = find_deepest_context(lookup_ln) if lookup_ln else None
            if not ctx:
                continue
            c = ctx
            while c and c.type != 'method':
                c = c.parent
            if c and c.type == 'method':
                class_parent = c.parent
                while class_parent and class_parent.type not in ('class', 'enum'):
                    class_parent = class_parent.parent
                class_name = class_parent.name if class_parent and class_parent.type in ('class', 'enum') else ''
                key = (class_name, 'method', c.name)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': c,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                entry['diff_lines'].append(raw_line)
                entry['body_changes'].append(raw_line)

        # Compute change_type per entry
        for key, info in grouped.items():
            elem_type = key[1]
            if elem_type == 'method':
                ctx = info.get('context')
                orig_len = (ctx.end_line - ctx.start_line + 1) if ctx else 0
                removed_lines = [l for l in info['diff_lines'] if l.startswith('-')]
                added_lines = [l for l in info['diff_lines'] if l.startswith('+')]
                # Check complete removal: all original lines removed, no additions
                if info['remove_sig'] and not info['add_sig'] and len(removed_lines) == orig_len:
                    info['computed_change_type'] = 'completely_removed'
                # Check complete addition: no original context, all lines are additions
                elif info['add_sig'] and not info['remove_sig'] and not ctx:
                    info['computed_change_type'] = 'completely_added'
                else:
                    info['computed_change_type'] = 'modified'
            else:
                adds = any(l.startswith('+') for l in info['diff_lines'])
                removes = any(l.startswith('-') for l in info['diff_lines'])
                if adds and not removes:
                    info['computed_change_type'] = 'added'
                elif removes and not adds:
                    info['computed_change_type'] = 'removed'
                else:
                    info['computed_change_type'] = 'modified'

        return grouped
