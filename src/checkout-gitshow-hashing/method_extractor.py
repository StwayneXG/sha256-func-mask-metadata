import re
import javalang

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
        elif "JSType" in trimmed_line and "{" in trimmed_line:
            if trimmed_line.endswith(";"):
                return False
            pre_comment = trimmed_line.split("//")[0].strip()
            if '=' in pre_comment:
                return False
            return True
        elif "class" in trimmed_line and "{" in trimmed_line:
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
        if "class" in trimmed_line:
            # Extract class name, which follows the 'class' keyword
            class_match = re.search(r'class\s+(\w+)', trimmed_line)
            if class_match:
                return class_match.group(1)
            return None
        
        # Case 2: General method or constructor extraction
        # This pattern captures method/constructor name before the '('
        method_match = re.search(r'(\w+)\s*\(', trimmed_line)
        if method_match:
            return method_match.group(1)

        return None

    @staticmethod
    def extract_methods(diff):
        lines = diff.split('\n')
        methods = {}
        current_file = None
        current_diff_line = ""

        for i, line in enumerate(lines):
            if line.startswith('+++'):
                current_file = line[4:].strip()
                script_logger.debug(f"Currently analyzing file: {current_file}")
                continue

            if line.startswith('+') or line.startswith('-'):
                current_diff_line = line.strip()  # Remove the '+' or '-' prefix
                script_logger.debug(f"Current diff line: {current_diff_line}")
                # Look upwards for the function declaration
                for j in range(i, -1, -1):
                    # If reach the start of the file without finding a function declaration, break
                    if lines[j].startswith('---') or lines[j].startswith('+++'):
                        script_logger.debug(f"Reached the start of the file without finding a function declaration in {current_file}")
                        break
                    is_function_line = MethodExtractor.is_function_line(lines[j])
                    if is_function_line and lines[j].startswith('-'):
                        script_logger.debug(f"Found function, but was added after patch fix, skipping current diff line.\nFunction line found: {lines[j]}")
                        break
                    elif is_function_line:
                        # if current_file not in method_lines:
                        #     method_lines[current_file] = set()
                        # method_lines[current_file].add(lines[j])

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

        return methods
    
    @staticmethod
    def extract_method_implementations(diff, methods, base_path):
        # method_implementations = {}

        # for file_path, method_names in methods.items():
        #     content = open(base_path + file_path, errors='ignore').read()
        #     try:
        #         tree = javalang.parse.parse(content)
        #     except javalang.parser.JavaSyntaxError:
        #         print(f"Error parsing file: {file_path}")
        #         continue

        #     for method_name in method_names:
        #         method_info = MethodExtractor._find_method(tree, method_name)
        #         if method_info:
        #             print(f"Method position for {method_name}: {method_info[0]}")
        #             print(f"Type for methodname0: {type(method_info[0])}")
        #             method_body = MethodExtractor._find_method_body(method_info[0], content)
        #             method_implementations[method_name] = method_body

        method_implementations = {}

        for file_path, method_name_line_pairs in methods.items():
            content = open(base_path + file_path, errors='ignore').read()
            try:
                tree = javalang.parse.parse(content)
            except javalang.parser.JavaSyntaxError:
                print(f"Error parsing file: {file_path}")
                continue

            for method_name, method_line in method_name_line_pairs:
                method_line_number = MethodExtractor._find_method_by_line(method_line, content)
                method_position = javalang.tokenizer.Position(method_line_number, 1)
                if method_line_number:
                    method_body = MethodExtractor._find_method_body(method_position, content)
                    method_implementations[method_name] = method_body

        # for (file_path, method_names), (file_path2, method_line_s) in zip(methods.items(), method_lines.items()):
        #     content = open(base_path + file_path, errors='ignore').read()
        #     try:
        #         tree = javalang.parse.parse(content)
        #     except javalang.parser.JavaSyntaxError:
        #         print(f"Error parsing file: {file_path}")
        #         continue

        #     for method_name, method_line in zip(method_names, method_line_s):
        #         method_line_number = MethodExtractor._find_method_by_line(method_line, content)
        #         method_position = javalang.tokenizer.Position(method_line_number, 1)
        #         if method_line_number:
        #             method_body = MethodExtractor._find_method_body(method_position, content)
        #             method_implementations[method_name] = method_body


        #     method_line_number = MethodExtractor._find_method_by_line(method_line, content)
        #     print(f"Method position for {method_line}: {method_line_number}")
        #     method_position = javalang.tokenizer.Position(method_line_number, 1)
        #     print(f"Method position for {method_line}: {method_position}")
        #     if method_line_number:
        #         method_body = MethodExtractor._find_method_body(method_position, content)
        #         print(f"Method body for {method_line}:\n{method_body}")
        #         print(f"Method Name: {method_name}")
        #         method_implementations[method_name] = method_body
        # print(f"Method implementations:\n{method_implementations}")
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
