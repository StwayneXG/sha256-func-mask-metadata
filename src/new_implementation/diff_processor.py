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
    (class, enum, method, member_variable, or import). Each element row includes
    the full source body of that element as 'element_source'.

    For methods, change_type becomes:
      - completely_added   (signature added, no removals)
      - completely_removed (signature removed with entire body removed)
      - modified           (any partial changes or mixed additions/removals)
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        script_logger.debug("Starting parse_diff_to_dataframe")
        per_file_changes = self._collect_changed_lines(diff_text)

        file_to_contexts = {}
        for fp in per_file_changes:
            script_logger.debug(f"Building contexts for file: {fp}")
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = build_contexts_for_file(full_disk)

        grouped = {}
        for fp, changes in per_file_changes.items():
            script_logger.debug(f"Grouping changes for file: {fp}")
            contexts = file_to_contexts.get(fp, [])
            grouped[fp] = self._group_changes_for_file(fp, changes, contexts)

        records = []
        for fp, groups in grouped.items():
            script_logger.debug(f"Flattening grouped results for file: {fp}")
            full_disk = os.path.join(self.repo_root, fp)
            file_lines = []
            if os.path.isfile(full_disk):
                with open(full_disk, 'r', encoding='utf-8') as f:
                    file_lines = f.read().splitlines()
            else:
                script_logger.warning(f"Cannot open {full_disk} to extract element source.")

            for (class_name, elem_type, elem_name), info in groups.items():
                change_type = info.get('computed_change_type')
                elem_src = ''
                if elem_type == 'method':
                    script_logger.debug(f"Processing method '{elem_name}' with change_type '{change_type}'")
                    if change_type != 'completely_removed':
                        ctx = info.get('context')
                        if ctx and file_lines:
                            start, end = ctx.start_line, ctx.end_line
                            elem_src = "\n".join(file_lines[start-1:end])
                elif elem_type == 'member_variable' and change_type != 'removed':
                    script_logger.debug(f"Processing member_variable '{elem_name}' with change_type '{change_type}'")
                    ctx = info.get('context')
                    if ctx and file_lines:
                        idx = ctx.start_line
                        elem_src = file_lines[idx-1]
                elif elem_type == 'import':
                    script_logger.debug(f"Processing import '{elem_name}' with change_type '{change_type}'")
                else:
                    script_logger.debug(f"Processing {elem_type} '{elem_name}' with change_type '{change_type}'")

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
        script_logger.debug("parse_diff_to_dataframe complete")
        return df

    def _collect_changed_lines(self, diff_text: str):
        script_logger.debug("Starting _collect_changed_lines")
        per_file_changes = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False
        hunk_re = re.compile(r'^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@')

        for raw in diff_text.splitlines():
            script_logger.debug(f"Reading diff line: {raw}")
            if raw.startswith('diff --git '):
                script_logger.debug("Found diff header, resetting current_file and in_hunk")
                current_file = None
                in_hunk = False
                continue
            if raw.startswith('index '):
                continue
            if raw.startswith('\\ No newline at end of file'):
                continue
            if raw.startswith('+++ '):
                path = raw[4:].strip()
                script_logger.debug(f"Processing file header +++: {path}")
                if path != '/dev/null':
                    current_file = path
                    per_file_changes[current_file] = []
                    script_logger.debug(f"Set current_file to {current_file}")
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
                script_logger.debug(f"Starting hunk: old={running_old}, new={running_new} in {current_file}")
                continue
            if not in_hunk or not current_file:
                continue
            if raw.startswith(' '):
                running_old += 1
                running_new += 1
                continue
            prefix = raw[0]
            if prefix == '-':
                script_logger.debug(f"Recording removal at old_lineno={running_old}: {raw}")
                per_file_changes[current_file].append({
                    'old_lineno': running_old,
                    'new_lineno': None,
                    'raw': raw
                })
                running_old += 1
            elif prefix == '+':
                script_logger.debug(f"Recording addition at new_lineno={running_new}: {raw}")
                per_file_changes[current_file].append({
                    'old_lineno': None,
                    'new_lineno': running_new,
                    'raw': raw
                })
                running_new += 1
            else:
                script_logger.debug(f"Unexpected line in hunk: {raw}")
        script_logger.debug("_collect_changed_lines complete")
        return per_file_changes

    def _group_changes_for_file(self, file_path, changes, contexts):
        script_logger.debug(f"Starting _group_changes_for_file for {file_path}")
        def find_deepest_context(line_no):
            cands = [c for c in contexts if c.start_line <= line_no <= c.end_line]
            return max(cands, key=lambda c: c.start_line) if cands else None

        grouped = {}
        removed_method_ranges = {}  # key -> (start_line, end_line)

        # Helper functions
        def handle_import(raw, key, prefix, entry):
            script_logger.debug(f"Detected import change: {key}")
            entry['diff_lines'].append(raw)
            if prefix == '+':
                entry['add_sig'] = True
            if prefix == '-':
                entry['remove_sig'] = True

        def handle_class_enum(raw, stripped, entry):
            script_logger.debug(f"Detected class/enum change: {stripped}")
            entry['diff_lines'].append(raw)
            entry['change_types'].add('modified')

        def handle_member_variable(raw, key, prefix, lookup_ln, entry):
            script_logger.debug(f"Detected member variable change: {key[2]}")
            entry['diff_lines'].append(raw)
            if prefix == '-':
                entry['remove_sig'] = True
                if lookup_ln:
                    field_ctx = find_deepest_context(lookup_ln)
                    if field_ctx and field_ctx.type == 'member_variable' and field_ctx.name == key[2]:
                        entry['context'] = field_ctx
            if prefix == '+':
                entry['add_sig'] = True

        def handle_method_signature(raw, stripped, lookup_ln, key, entry):
            script_logger.debug(f"Detected method signature change: {stripped}")
            entry['diff_lines'].append(raw)
            if raw.startswith('-'):
                entry['remove_sig'] = True
                script_logger.debug("Method signature removal logged")
                if lookup_ln:
                    method_ctx = find_deepest_context(lookup_ln)
                    if method_ctx and method_ctx.type == 'method' and method_ctx.name == key[2]:
                        entry['context'] = method_ctx
                        removed_method_ranges[key] = (method_ctx.start_line, method_ctx.end_line)
                        script_logger.debug(f"Marked method {key} as removed range {method_ctx.start_line}-{method_ctx.end_line}")
            if raw.startswith('+'):
                entry['add_sig'] = True
                script_logger.debug("Method signature addition logged")

        def handle_body_change(raw, lookup_ln, key, entry):
            script_logger.debug(f"Assigning body change to method {key}")
            entry['diff_lines'].append(raw)
            entry['body_changes'].append(lookup_ln)

        # First pass: identify fully removed methods
        for item in changes:
            raw_line = item['raw']
            prefix = raw_line[0]
            content = raw_line[1:].strip()
            if prefix == '-' and MethodExtractor.is_function_line(content):
                mname = MethodExtractor.extract_method_name(content) or 'N/A'
                old_ln = item['old_lineno']
                script_logger.debug(f"Checking potential full removal: {content} at {old_ln}")
                parent = find_deepest_context(old_ln) if old_ln else None
                while parent and parent.type not in ('class', 'enum'):
                    parent = parent.parent
                if parent and parent.type == 'method' and parent.name == mname:
                    key = (parent.parent.name if parent.parent else '', 'method', mname)
                    removed_method_ranges[key] = (parent.start_line, parent.end_line)
                    script_logger.debug(f"Marked full removal range for {key}: {parent.start_line}-{parent.end_line}")

        # Second pass: handle each change via helper
        for item in changes:
            old_ln = item['old_lineno']
            new_ln = item['new_lineno']
            raw_line = item['raw']
            prefix = raw_line[0]
            content = raw_line[1:].rstrip()
            stripped = content.strip()
            script_logger.debug(f"Processing change line: {raw_line}")

            # If falls in removed_method_ranges
            if old_ln is not None:
                for mkey, (start, end) in removed_method_ranges.items():
                    if start <= old_ln <= end:
                        entry = grouped.setdefault(mkey, {
                            'diff_lines': [], 'context': None,
                            'remove_sig': False, 'add_sig': False,
                            'body_changes': [], 'change_types': set()
                        })
                        handle_method_signature(raw_line, stripped, old_ln, mkey, entry)
                        entry['computed_change_type'] = 'completely_removed'
                        script_logger.debug(f"Assigned line to completely_removed method {mkey}")
                        break
                else:
                    pass
                if any(start <= old_ln <= end for (start, end) in removed_method_ranges.values()):
                    continue

            # IMPORT
            if stripped.startswith('import '):
                key = ('', 'import', stripped)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': None,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                handle_import(raw_line, key, prefix, entry)
                continue

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
                handle_class_enum(raw_line, stripped, entry)
                continue

            # METHOD SIGNATURE (partial mod/add)
            if MethodExtractor.is_function_line(stripped) and '(' in stripped:
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ('class', 'enum'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class', 'enum') else ''
                mname = MethodExtractor.extract_method_name(stripped) or 'N/A'
                key = (class_name, 'method', mname)
                entry = grouped.setdefault(key, {
                    'diff_lines': [], 'context': None,
                    'remove_sig': False, 'add_sig': False,
                    'body_changes': [], 'change_types': set()
                })
                handle_method_signature(raw_line, stripped, lookup_ln, key, entry)
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
                handle_member_variable(raw_line, key, prefix, lookup_ln, entry)
                continue

            # BODY CHANGE INSIDE METHOD (partial)
            lookup_ln = old_ln if old_ln is not None else new_ln
            ctx = find_deepest_context(lookup_ln) if lookup_ln else None
            if not ctx:
                script_logger.debug("No context found for body change line")
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
                handle_body_change(raw_line, lookup_ln, key, entry)

        # Compute change_type
        script_logger.debug("Computing final change types for grouped elements")
        for key, info in grouped.items():
            elem_type = key[1]
            if elem_type == 'method':
                if info.get('computed_change_type') == 'completely_removed':
                    continue
                if info['remove_sig'] and not info['add_sig'] and key in removed_method_ranges:
                    info['computed_change_type'] = 'completely_removed'
                    script_logger.debug(f"Method {key} labeled completely_removed")
                elif info['add_sig'] and not info['remove_sig']:
                    info['computed_change_type'] = 'completely_added'
                    script_logger.debug(f"Method {key} labeled completely_added")
                else:
                    info['computed_change_type'] = 'modified'
                    script_logger.debug(f"Method {key} labeled modified")
            elif elem_type == 'import':
                adds = info['add_sig']
                removes = info['remove_sig']
                if adds and not removes:
                    info['computed_change_type'] = 'added'
                elif removes and not adds:
                    info['computed_change_type'] = 'removed'
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

        script_logger.debug(f"_group_changes_for_file complete for {file_path}")
        return grouped

    # Helper functions for clarity could be added here if needed
