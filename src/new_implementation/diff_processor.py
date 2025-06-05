import os
import re
import logging
import pandas as pd
from collections import defaultdict

# Import our helper modules
from method_extractor import MethodExtractor
from context_builder import build_contexts_for_file, _Context

# Configure or get the same logger
script_logger = logging.getLogger("diff_processor")

class DiffProcessor:
    """
    Parses a unified diff and produces a DataFrame of changed elements (class, method, member variable).
    Each method row also includes the full source body of that method.
    """

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def parse_diff_to_dataframe(self, diff_text: str) -> pd.DataFrame:
        """
        Given a unified diff string (no a/ or b/ prefixes), return a DataFrame with columns:
          - file_path
          - package
          - class_name
          - element_type    (class | enum | method | member_variable)
          - element_name
          - change_type     (added | removed | modified)
          - diff_lines      (list of raw +/− lines)
          - method_source   (full source code for methods, empty for others)
        """
        # 1) Collect changed lines per file
        per_file_changes = self._collect_changed_lines(diff_text)

        # 2) Build contexts per file using original Java files
        file_to_contexts = {}
        for fp in per_file_changes:
            full_disk = os.path.join(self.repo_root, fp)
            file_to_contexts[fp] = build_contexts_for_file(full_disk)

        # 3) Group changed lines per file
        grouped = {}
        for fp, changes in per_file_changes.items():
            contexts = file_to_contexts.get(fp, [])
            grouped[fp] = self._group_changes_for_file(fp, changes, contexts)

        # 4) Flatten to records, add method_source for methods
        records = []
        for fp, groups in grouped.items():
            # For reading method source, load all lines once
            full_disk = os.path.join(self.repo_root, fp)
            file_lines = []
            if os.path.isfile(full_disk):
                with open(full_disk, 'r', encoding='utf-8') as f:
                    file_lines = f.read().splitlines()
            else:
                script_logger.warning(f"Cannot open {full_disk} to extract method source.")

            for (class_name, elem_type, elem_name), info in groups.items():
                pkg = info['package']
                cset = info['change_types']
                if cset == {'added'}:
                    cfinal = 'added'
                elif cset == {'removed'}:
                    cfinal = 'removed'
                else:
                    cfinal = 'modified'

                # Determine method_source if this is a method
                method_src = ''
                if elem_type == 'method':
                    ctx = info.get('context')  # should be a _Context for the method
                    if ctx and file_lines:
                        start, end = ctx.start_line, ctx.end_line
                        # Convert 1-based to 0-based list indices
                        method_src = "\n".join(file_lines[start-1:end])
                    else:
                        script_logger.warning(f"No context or file lines for method {elem_name} in {fp}.")
                        method_src = ''

                records.append({
                    'file_path': fp,
                    'package': pkg,
                    'class_name': class_name,
                    'element_type': elem_type,
                    'element_name': elem_name,
                    'change_type': cfinal,
                    'diff_lines': info['diff_lines'],
                    'method_source': method_src
                })

        df = pd.DataFrame(records, columns=[
            'file_path', 'package', 'class_name', 'element_type',
            'element_name', 'change_type', 'diff_lines', 'method_source'
        ])
        return df

    def _collect_changed_lines(self, diff_text: str):
        """
        Step 1: Parse diff_text to collect a list of changed lines for each file.
        Returns: dict[file_path] -> list of dicts {old_lineno, new_lineno, raw}
        """
        per_file_changes = defaultdict(list)
        current_file = None
        running_old = None
        running_new = None
        in_hunk = False

        hunk_re = re.compile(r'^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@')

        for raw in diff_text.splitlines():
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
                if not raw.lstrip().startswith('- import '):
                    per_file_changes[current_file].append({
                        'old_lineno': running_old,
                        'new_lineno': None,
                        'raw': raw
                    })
                else:
                    script_logger.debug(f"Ignoring import‐line removal: {raw[1:].strip()}")
                running_old += 1
            elif prefix == '+':
                if not raw.lstrip().startswith('+ import '):
                    per_file_changes[current_file].append({
                        'old_lineno': None,
                        'new_lineno': running_new,
                        'raw': raw
                    })
                else:
                    script_logger.debug(f"Ignoring import‐line addition: {raw[1:].strip()}")
                running_new += 1
            else:
                script_logger.debug(f"Unexpected line in hunk: {raw}")

        return per_file_changes

    def _group_changes_for_file(self, file_path, changes, contexts):
        """
        Step 3: Group changed lines by (class_name, element_type, element_name).
        Skip any lines not in a class or method.  We only produce rows for:
          - class declarations
          - enum declarations
          - method declarations or body changes
          - member variable declarations

        Returns a dict: key -> {
            'package': ..., 'change_types': set(...), 'diff_lines': [...], 'context': <_Context or None>
        }
        where key = (class_name, element_type, element_name).
        """
        def find_deepest_context(line_no):
            cands = [c for c in contexts if c.start_line <= line_no <= c.end_line]
            return max(cands, key=lambda c: c.start_line) if cands else None

        grouped = {}
        for item in changes:
            old_ln = item['old_lineno']
            new_ln = item['new_lineno']
            raw_line = item['raw']
            change_type = 'added' if raw_line.startswith('+') else 'removed'
            content = raw_line[1:].rstrip()
            stripped = content.strip()

            # 3.1) Class or enum declaration in diff
            if stripped.startswith('class ') or ' class ' in stripped or \
               stripped.startswith('enum ') or ' enum ' in stripped:
                name = MethodExtractor.extract_method_name(stripped) or 'N/A'
                pkg_ctx = next((c for c in contexts if c.type == 'package'), None)
                pkg = pkg_ctx.name if pkg_ctx else ''
                key = (name, 'class', name)
                if key not in grouped:
                    grouped[key] = {
                        'package': pkg,
                        'change_types': set(),
                        'diff_lines': [],
                        'context': None
                    }
                grouped[key]['change_types'].add(change_type)
                grouped[key]['diff_lines'].append(raw_line)
                continue

            # 3.2) Method declaration in diff
            if MethodExtractor.is_function_line(stripped) and '(' in stripped:
                mname = MethodExtractor.extract_method_name(stripped) or 'N/A'
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ('class', 'enum', 'package'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class', 'enum') else ''
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=='package') else ''
                key = (class_name, 'method', mname)
                if key not in grouped:
                    grouped[key] = {
                        'package': pkg,
                        'change_types': set(),
                        'diff_lines': [],
                        'context': None
                    }
                grouped[key]['change_types'].add(change_type)
                grouped[key]['diff_lines'].append(raw_line)
                # Also store the method's context if found in original
                if lookup_ln:
                    method_ctx = find_deepest_context(lookup_ln)
                    if method_ctx and method_ctx.type == 'method' and method_ctx.name == mname:
                        grouped[key]['context'] = method_ctx
                continue

            # 3.3) Member variable declaration in diff
            if MethodExtractor.is_member_variable(stripped):
                varname = MethodExtractor.extract_variable_name(stripped) or 'N/A'
                lookup_ln = old_ln if old_ln is not None else new_ln
                parent = find_deepest_context(lookup_ln) if lookup_ln else None
                while parent and parent.type not in ('class', 'enum', 'package'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class', 'enum') else ''
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=='package') else ''
                key = (class_name, 'member_variable', varname)
                if key not in grouped:
                    grouped[key] = {
                        'package': pkg,
                        'change_types': set(),
                        'diff_lines': [],
                        'context': None
                    }
                grouped[key]['change_types'].add(change_type)
                grouped[key]['diff_lines'].append(raw_line)
                continue

            # 3.4) Method body change or removal/add inside a method
            lookup_ln = old_ln if old_ln is not None else new_ln
            ctx = find_deepest_context(lookup_ln) if lookup_ln else None
            if not ctx:
                # Skip changes not in any context (no class or method)
                continue
            # Find the method context in ancestors
            c = ctx
            while c and c.type != 'method':
                c = c.parent
            if c and c.type == 'method':
                parent = c.parent
                while parent and parent.type not in ('class', 'enum'):
                    parent = parent.parent
                class_name = parent.name if parent and parent.type in ('class','enum') else ''
                pkg = parent.parent.name if (parent and parent.parent and parent.parent.type=='package') else ''
                element_type = 'method'
                element_name = c.name
                key = (class_name, element_type, element_name)
                if key not in grouped:
                    grouped[key] = {
                        'package': pkg,
                        'change_types': set(),
                        'diff_lines': [],
                        'context': c
                    }
                grouped[key]['change_types'].add(change_type)
                grouped[key]['diff_lines'].append(raw_line)
            # Else: skip anything not in a method

        return grouped
