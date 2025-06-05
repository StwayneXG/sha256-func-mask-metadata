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
    Parses a unified diff and produces a DataFrame of changed elements:
      - import
      - class
      - method
      - member_variable

    Simplified rules:
    1. We only look up context for added lines, since removed elements do not exist on disk.
    2. If removed lines fall inside a method body, we group them under that method (extract method context).
    3. If a method signature is removed and every line from its start to its end is also removed (all '-'), we label it completely_removed.
    4. Otherwise, classify added vs removed vs modified for imports/fields and methods.
    5. Finally, emit one extra row per class containing the entire class implementation if any change inside it.
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

        grouped_per_file = {}
        for fp, changes in per_file_changes.items():
            contexts = file_to_contexts.get(fp, [])
            grouped_per_file[fp] = self._group_changes_for_file(fp, changes, contexts)

        rows = []
        for fp, groups in grouped_per_file.items():
            full_disk = os.path.join(self.repo_root, fp)
            try:
                with open(full_disk, 'r', encoding='utf-8') as f:
                    file_lines = f.read().splitlines()
            except FileNotFoundError:
                file_lines = []
                script_logger.warning(f"Cannot open {full_disk} to extract source.")

            classes_with_changes = set()
            for (class_name, elem_type, elem_name), info in groups.items():
                change_type = info['computed_change_type']
                diff_lines = info['diff_lines']
                ctx = info.get('context')

                if elem_type == 'method':
                    if change_type == 'completely_removed':
                        elem_src = None
                    else:
                        if ctx and file_lines:
                            start, end = ctx.start_line, ctx.end_line
                            elem_src = "\n".join(file_lines[start-1:end])
                        else:
                            elem_src = ''
                    classes_with_changes.add(class_name)
                elif elem_type == 'member_variable':
                    if change_type == 'removed':
                        elem_src = None
                    else:
                        if ctx and file_lines:
                            idx = ctx.start_line
                            elem_src = file_lines[idx-1]
                        else:
                            elem_src = ''
                    classes_with_changes.add(class_name)
                elif elem_type == 'import':
                    elem_src = None
                else:
                    elem_src = ''

                rows.append({
                    'file_path': fp,
                    'class_name': class_name,
                    'element_type': elem_type,
                    'element_name': elem_name,
                    'change_type': change_type,
                    'diff_lines': diff_lines,
                    'element_source': elem_src
                })

            # Emit class rows
            contexts = file_to_contexts.get(fp, [])
            for cname in classes_with_changes:
                class_ctx = next((c for c in contexts if c.type=='class' and c.name==cname), None)
                if not class_ctx:
                    continue
                if file_lines:
                    s,e = class_ctx.start_line, class_ctx.end_line
                    class_src = "\n".join(file_lines[s-1:e])
                else:
                    class_src = ''
                rows.append({
                    'file_path': fp,
                    'class_name': cname,
                    'element_type': 'class',
                    'element_name': cname,
                    'change_type': 'modified',
                    'diff_lines': [],
                    'element_source': class_src
                })

        df = pd.DataFrame(rows, columns=[
            'file_path','class_name','element_type','element_name','change_type','diff_lines','element_source'
        ])
        script_logger.debug("parse_diff_to_dataframe complete")
        return df

    def _collect_changed_lines(self, diff_text: str):
        script_logger.debug("Starting _collect_changed_lines")
        per_file = defaultdict(list)
        current_file = None
        running_old = running_new = None
        in_hunk = False
        hunk_re = re.compile(r'^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@')

        for raw in diff_text.splitlines():
            script_logger.debug(f"Reading diff line: {raw}")
            if raw.startswith('diff --git '):
                current_file = None; in_hunk = False; continue
            if raw.startswith('index '): continue
            if raw.startswith('\\ No newline at end of file'): continue
            if raw.startswith('+++ '):
                path = raw[4:].strip()
                if path.endswith('.java') and path != '/dev/null':
                    current_file = path; per_file[current_file]=[]
                    script_logger.debug(f"Set current_file to {current_file}")
                else:
                    current_file = None
                in_hunk = False; continue
            if raw.startswith('--- '): continue
            m = hunk_re.match(raw)
            if m and current_file:
                running_old = int(m.group(1)); running_new = int(m.group(2)); in_hunk=True
                script_logger.debug(f"Entering hunk for {current_file}: old={running_old} new={running_new}")
                continue
            if not in_hunk or not current_file: continue
            if raw.startswith(' '): running_old+=1; running_new+=1; continue
            prefix=raw[0]
            if prefix=='-':
                per_file[current_file].append({'old_lineno':running_old,'new_lineno':None,'raw':raw}); running_old+=1
            elif prefix=='+':
                per_file[current_file].append({'old_lineno':None,'new_lineno':running_new,'raw':raw}); running_new+=1
            else:
                script_logger.debug(f"Unexpected line in hunk: {raw}")
        script_logger.debug("_collect_changed_lines complete")
        return per_file

    def _group_changes_for_file(self, file_path, changes, contexts):
        script_logger.debug(f"Starting _group_changes_for_file for {file_path}")
        def find_context(line_no):
            return max((c for c in contexts if c.start_line<=line_no<=c.end_line), key=lambda c:c.start_line, default=None)

        grouped = {}
        removed_ranges = {}

        # Detect fully removed methods
        # Collect all old signature lines
        old_sigs=[]
        for c in changes:
            if c['raw'].startswith('-'):
                stripped=c['raw'][1:].strip()
                if MethodExtractor.is_function_line(stripped) and '(' in stripped:
                    old_sigs.append((c['old_lineno'], stripped))
        # Check each
        all_removed_old={c['old_lineno'] for c in changes if c['raw'].startswith('-') and c['old_lineno'] is not None}
        for old_ln, sig in old_sigs:
            parent=find_context(old_ln)
            while parent and parent.type!='method': parent=parent.parent
            if not parent: continue
            key=(parent.parent.name if parent.parent else '', 'method', parent.name)
            rng=set(range(parent.start_line, parent.end_line+1))
            if rng.issubset(all_removed_old):
                removed_ranges[key]=(parent.start_line, parent.end_line)
                script_logger.debug(f"Marked {key} as completely_removed range {parent.start_line}-{parent.end_line}")

        for c in changes:
            old_ln=c['old_lineno']; new_ln=c['new_lineno']; raw=c['raw']; prefix=raw[0]; content=raw[1:].strip();
            script_logger.debug(f"Processing line: {raw}")
            # Fully removed method
            if old_ln is not None:
                for key,(s,e) in removed_ranges.items():
                    if s<=old_ln<=e and raw.startswith('-'):
                        entry=grouped.setdefault(key, {'diff_lines':[],'context':None,'remove_sig':True,'add_sig':False,'body_changes':[],'change_types':set()})
                        entry['diff_lines'].append(raw)
                        entry['context']=_Context('method', key[2], s, e, '', None)
                        entry['computed_change_type']='completely_removed'
                        script_logger.debug(f"Assigned {raw} to removed method {key}")
                        break
                else: pass
                if any(s<=old_ln<=e for s,e in removed_ranges.values()): continue
            # IMPORT
            if content.startswith('import '):
                key=('', 'import', content)
                entry=grouped.setdefault(key, {'diff_lines':[],'context':None,'remove_sig':False,'add_sig':False,'body_changes':[],'change_types':set()})
                entry['diff_lines'].append(raw)
                if prefix=='+': entry['add_sig']=True
                if prefix=='-': entry['remove_sig']=True
                continue
            # MEMBER VARIABLE
            if MethodExtractor.is_member_variable(content):
                lookup=old_ln if old_ln is not None else new_ln
                parent=find_context(lookup) if lookup else None
                while parent and parent.type not in ('class','enum'): parent=parent.parent
                cls=parent.name if parent and parent.type in ('class','enum') else ''
                var=MethodExtractor.extract_variable_name(content) or 'N/A'
                key=(cls, 'member_variable', var)
                entry=grouped.setdefault(key, {'diff_lines':[],'context':None,'remove_sig':False,'add_sig':False,'body_changes':[],'change_types':set()})
                entry['diff_lines'].append(raw)
                if prefix=='-': entry['remove_sig']=True
                if prefix=='+': entry['add_sig']=True
                if prefix=='-' and lookup:
                    fctx=find_context(lookup)
                    if fctx and fctx.type=='member_variable' and fctx.name==var:
                        entry['context']=fctx
                continue
            # METHOD SIGNATURE ADD (only) => context lookup
            if prefix=='+' and MethodExtractor.is_function_line(content) and '(' in content:
                lookup=new_ln
                parent=find_context(lookup)
                while parent and parent.type not in ('class','enum'): parent=parent.parent
                cls=parent.name if parent and parent.type in ('class','enum') else ''
                m=MethodExtractor.extract_method_name(content) or 'N/A'
                key=(cls,'method',m)
                entry=grouped.setdefault(key, {'diff_lines':[],'context':None,'remove_sig':False,'add_sig':False,'body_changes':[],'change_types':set()})
                entry['diff_lines'].append(raw)
                entry['add_sig']=True
                if lookup:
                    mctx=find_context(lookup)
                    if mctx and mctx.type=='method' and mctx.name==m:
                        entry['context']=mctx
                continue
            # BODY CHANGE INSIDE METHOD (removed inside body)
            if prefix=='-' or prefix=='+':
                lookup=old_ln if prefix=='-' else new_ln
                ctx=find_context(lookup)
                c=ctx
                while c and c.type!='method': c=c.parent
                if c and c.type=='method':
                    cls=c.parent.name if c.parent and c.parent.type in ('class','enum') else ''
                    key=(cls,'method',c.name)
                    entry=grouped.setdefault(key, {'diff_lines':[],'context':c,'remove_sig':False,'add_sig':False,'body_changes':[],'change_types':set()})
                    entry['diff_lines'].append(raw)
                    if prefix=='-': entry['remove_sig']=True; entry['body_changes'].append(lookup)
                    if prefix=='+': entry['add_sig']=True
                    continue
            script_logger.debug(f"No match for line {raw}")

        # Compute change_type
        script_logger.debug("Computing final change types")
        for key,info in grouped.items():
            et=key[1]
            if et=='method':
                if info.get('computed_change_type')=='completely_removed': continue
                if info['add_sig'] and not info['remove_sig']: info['computed_change_type']='completely_added'
                elif info['remove_sig'] and not info['add_sig'] and key in removed_ranges: info['computed_change_type']='completely_removed'
                else: info['computed_change_type']='modified'
            elif et=='import':
                adds=info['add_sig']; rem=info['remove_sig']
                if adds and not rem: info['computed_change_type']='added'
                elif rem and not adds: info['computed_change_type']='removed'
                else: info['computed_change_type']='modified'
            else:
                adds=any(l.startswith('+') for l in info['diff_lines'])
                rem=any(l.startswith('-') for l in info['diff_lines'])
                if adds and not rem: info['computed_change_type']='added'
                elif rem and not adds: info['computed_change_type']='removed'
                else: info['computed_change_type']='modified'

        script_logger.debug(f"_group_changes_for_file complete for {file_path}")
        return grouped
