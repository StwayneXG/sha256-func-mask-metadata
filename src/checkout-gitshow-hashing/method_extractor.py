import re
import javalang
import os

from config import logging_level
from logging_utils import get_console_logger
script_logger = get_console_logger(__name__, level=logging_level)

class MethodExtractor:
    @staticmethod
    def is_function_line(line):
        trimmed_line = line.strip()
        if trimmed_line.startswith("//"):
            return False
        
        if "(" in trimmed_line and any(keyword in trimmed_line for keyword in ["private", "protected", "public", "static", "void"]):
            if trimmed_line.endswith(";"):
                return False
            pre_comment = trimmed_line.split("//")[0].strip()
            if '=' in pre_comment:
                return False
            return True
        elif "class " in trimmed_line:
            if trimmed_line.endswith(";"):
                return False
            pre_comment = trimmed_line.split("//")[0].strip()
            if '=' in pre_comment:
                return False
            return True
        
        return False

    @staticmethod
    def extract_method_name(line):
        # Trim the line for clean matching
        trimmed_line = line.strip()
        
        # Case 1: Check if it's a class declaration
        if "class " in trimmed_line:
            # Extract class name, which follows the 'class' keyword
            class_match = re.search(r'class\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed_line)
            if class_match:
                return class_match.group(1)
            return None
        
        # Case 2: General method or constructor extraction
        # This pattern captures method/constructor name before the '('
        method_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', trimmed_line)
        if method_match:
            return method_match.group(1)

        return None
    
    @staticmethod
    def is_member_variable(line):
        line = line.strip()

        if any(keyword in line for keyword in ["private", "protected", "public", "static"]):
            # Exclude methods: lines with () and no '='
            if '(' in line and ')' in line and '=' not in line:
                return False
            # Must end with semicolon
            if line.endswith(';'):
                return True

        return False
    
    @staticmethod
    def extract_variable_name(line):
        line = line.strip()

        # If there's an '=', take only the part before it
        if '=' in line:
            left_part = line.split('=')[0].strip()
        else:
            left_part = line

        # Split into tokens
        tokens = left_part.replace(",", " ").split()

        if not tokens:
            return None

        # The last token is almost always the variable name
        return tokens[-1]

    @staticmethod
    def extract_methods(diff):
        lines = diff.split('\n')
        methods = {}
        current_file = None
        current_diff_line = ""

        newly_added_methods = set()
        for i, line in enumerate(lines):
            if line.startswith('+++'):
                current_file = line[4:].strip()
                if not current_file.strip().endswith('.java'):
                    script_logger.debug(f"Skipping non-Java file: {current_file}")
                    current_file = None
                    continue
                else:
                    script_logger.debug(f"Currently analyzing file: {current_file}")
                    continue
            elif line.startswith('---'):
                continue

            if line.startswith('+') or line.startswith('-'):
                if current_file is None:
                    script_logger.debug(f"No current file set, skipping line: {line}")
                    continue
                current_diff_line = line.strip()  # Remove the '+' or '-' prefix
                script_logger.debug(f"Current diff line: {current_diff_line}")

                # Look upwards for the function declaration
                for j in range(i, -1, -1):
                    # If reach the start of the file without finding a function declaration, break
                    if lines[j].startswith('---') or lines[j].startswith('+++'):
                        script_logger.debug(f"Reached the start of the file without finding a function declaration in {current_file}")
                        break
                    
                    is_member_variable = MethodExtractor.is_member_variable(lines[j])
                    if is_member_variable:
                        if current_file not in methods:
                            methods[current_file] = set()
                        variable_name = MethodExtractor.extract_variable_name(lines[j])
                        methods[current_file].add(variable_name, lines[j])
                        break

                    is_function_line = MethodExtractor.is_function_line(lines[j])
                    if is_function_line and lines[j].startswith('-'):
                        newly_added_methods.add(lines[j][1:].strip())
                        # script_logger.warning(f"Found function, but was added after patch fix, skipping current diff line.\nFunction line found: {lines[j]}")
                        break

                    elif is_function_line:
                        if lines[j].startswith('+'):
                            # Remove the '+' prefix
                            lines[j] = lines[j][1:]

                        script_logger.debug(f"Found function declaration: {lines[j]}")
                        
                        method_name = MethodExtractor.extract_method_name(lines[j])
                        script_logger.debug(f"Extracted method name: {method_name} from line: {lines[j]}")

                        if method_name:
                            if current_file not in methods:
                                methods[current_file] = set()
                            methods[current_file].add((method_name, lines[j]))
                        break  # Stop looking once we've found the function declaration

        return methods, newly_added_methods
    
    @staticmethod
    def extract_method_implementations(diff, methods, base_path):
        method_implementations = {}

        for file_path, method_name_line_pairs in methods.items():
            abs_path = os.path.join(base_path, file_path)
            try:
                with open(abs_path, errors='ignore') as f:
                    content = f.read()
            except FileNotFoundError:
                print(f"File not found: {abs_path}")
                continue

            try:
                tree = javalang.parse.parse(content)
            except javalang.parser.JavaSyntaxError:
                print(f"Error parsing file: {file_path}")
                continue

            file_method_map = {}

            for method_name, method_line in method_name_line_pairs:
                method_line_number = MethodExtractor._find_method_by_line(method_line, content)
                if not method_line_number:
                    script_logger.warning(f"Method line not found in content for method: {method_line} in file: {abs_path}")
                    continue

                method_position = javalang.tokenizer.Position(method_line_number, 1)
                method_body = MethodExtractor._find_method_body(method_position, content)

                if method_body:
                    file_method_map[(method_name, method_line_number)] = method_body

            if file_method_map:
                method_implementations[file_path] = file_method_map

        return method_implementations


    @staticmethod
    def _find_method(tree, method_name: str):
        for path, node in tree.filter(javalang.tree.MethodDeclaration):
            
            if node.name == method_name:
                return node.position, node.documentation
        for path, node in tree.filter(javalang.tree.ConstructorDeclaration):
            if node.name == method_name:
                return node.position, node.documentation
        return None

    @staticmethod
    def _find_method_by_line(method_line: str, content: str) -> str:
        lines = content.split('\n')
        # print(f"Looking for method line:\n{method_line}")
        for i, line in enumerate(lines):
            if method_line.strip() == line.strip():
                return i + 1

    @staticmethod
    def _find_method_body(start_position, content: str) -> str:
        lines = content.split('\n')
        current_line = start_position[0] - 1
        current_column = start_position[1] - 1
        brace_count = 0
        in_string = False
        in_char = False
        in_block_comment = False
        escape_next = False
        method_lines = []

        while current_line < len(lines):
            line = lines[current_line]
            i = current_column if current_line == start_position[0] - 1 else 0
            in_line_comment = False

            while i < len(line):
                char = line[i]

                if escape_next:
                    escape_next = False
                elif char == '\\':
                    escape_next = True
                elif in_string:
                    if char == '"':
                        in_string = False
                elif in_char:
                    if char == "'":
                        in_char = False
                elif in_line_comment:
                    pass
                elif in_block_comment:
                    if char == '*' and i + 1 < len(line) and line[i + 1] == '/':
                        in_block_comment = False
                        i += 1
                else:
                    if char == '"':
                        in_string = True
                    elif char == "'":
                        in_char = True
                    elif char == '/' and i + 1 < len(line):
                        if line[i + 1] == '/':
                            in_line_comment = True
                            i += 1
                        elif line[i + 1] == '*':
                            in_block_comment = True
                            i += 1
                    elif char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            method_lines.append(line[:i + 1])
                            return '\n'.join(method_lines)

                i += 1

            method_lines.append(line)
            current_line += 1
            current_column = 0

        return '\n'.join(method_lines)
