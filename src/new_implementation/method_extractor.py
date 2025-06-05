import re
from typing import List, Dict

class MethodExtractor:
    """
    Helper methods to detect function signatures, class/enum declarations,
    and member variable (field) declarations in Java source lines.
    """
    @staticmethod
    def is_function_line(line: str) -> bool:
        """Return True if 'line' looks like a method or constructor or class/enum declaration."""
        trimmed = line.strip()
        if trimmed.startswith("//"):
            return False
        # Detect method or constructor: has '(' and one of the modifiers or return types
        if "(" in trimmed and any(k in trimmed for k in ["private", "protected", "public", "static", "void"]):
            # Exclude field-like lines: if ends with ';' and no '='
            if trimmed.endswith(";"):
                return False
            pre_comment = trimmed.split("//")[0].strip()
            if "=" in pre_comment:
                return False
            return True
        # Detect class or enum or interface declaration
        if ("class " in trimmed or " enum " in trimmed or " interface " in trimmed
            or trimmed.startswith("class ") or trimmed.startswith("enum ") or trimmed.startswith("interface ")):
            if trimmed.endswith(";"):
                return False
            pre_comment = trimmed.split("//")[0].strip()
            if "=" in pre_comment:
                return False
            return True
        return False

    @staticmethod
    def extract_method_name(line: str) -> str:
        """Extract the method/constructor or class/enum name from a declaration line."""
        trimmed = line.strip()
        # Case 1: class, interface or enum
        m_type = re.search(r'\b(class|enum|interface)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
        if m_type:
            return m_type.group(2)
        # Case 2: method or constructor (name before '(')
        m_method = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', trimmed)
        if m_method:
            return m_method.group(1)
        return None

    @staticmethod
    def is_member_variable(line: str) -> bool:
        """Return True if 'line' looks like a field declaration in a class."""
        stripped = line.strip()
        if any(k in stripped for k in ["private", "protected", "public", "static"]):
            # Exclude methods: if it has "(" and ")" and no "="
            if "(" in stripped and ")" in stripped and "=" not in stripped:
                return False
            # Must end with semicolon
            if stripped.endswith(";"):
                return True
        return False

    @staticmethod
    def extract_variable_name(line: str) -> str:
        """Extract the variable name from a field declaration line."""
        stripped = line.strip()
        if "=" in stripped:
            left = stripped.split("=")[0].strip()
        else:
            left = stripped
        tokens = left.replace(",", " ").split()
        return tokens[-1] if tokens else None

    @staticmethod
    def find_body_end(start_line_idx: int, start_col_idx: int, lines: List[str]) -> int:
        """
        Helper to find matching closing brace ignoring comments/strings.
        Returns 1-based line number of the closing brace.
        """
        total_lines = lines
        brace_count = 0
        in_string = False
        in_char = False
        in_block_comment = False
        escape_next = False
        current_line = start_line_idx
        current_col = start_col_idx

        while current_line < len(total_lines):
            line = total_lines[current_line]
            i = current_col if current_line == start_line_idx else 0
            in_line_comment = False
            while i < len(line):
                char = line[i]
                if escape_next:
                    escape_next = False
                elif char == "\\":
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
                            # Return 1-based line number for the closing brace
                            return current_line + 1
                i += 1
            current_line += 1
            current_col = 0
        return None

def extract_java_context(file_path: str) -> List[Dict]:
    """
    Reads a Java file and extracts all relevant declarations:
    - package
    - import statements
    - class / interface / enum declarations (with extends/implements)
    - method declarations (with body spans)
    - member variables

    Returns a list of dictionaries with keys:
    - element_name
    - type               (one of "package", "import", "class", "interface", "enum",
                          "method", "member_variable")
    - start_line         (1-based)
    - end_line           (1-based; same as start_line for single-line items)
    - extends            (list of superclass names, if applicable)
    - implements         (list of interface names, if applicable)
    - return_type        (for methods; None otherwise)
    - parameters         (for methods; list of (type, name) tuples; empty list if none)
    - data_type          (for member variables; None otherwise)
    """
    context: List[Dict] = []
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()

        # 1) package declaration
        if stripped.startswith("package ") and stripped.endswith(";"):
            pkg_name = stripped[len("package "):-1].strip()
            context.append({
                "element_name": pkg_name,
                "type": "package",
                "start_line": idx + 1,
                "end_line": idx + 1,
                "extends": [],
                "implements": [],
                "return_type": None,
                "parameters": [],
                "data_type": None
            })
            continue

        # 2) import statement
        if stripped.startswith("import ") and stripped.endswith(";"):
            imp_name = stripped[len("import "):-1].strip()
            context.append({
                "element_name": imp_name,
                "type": "import",
                "start_line": idx + 1,
                "end_line": idx + 1,
                "extends": [],
                "implements": [],
                "return_type": None,
                "parameters": [],
                "data_type": None
            })
            continue

        # 3) member variable (field) declarations
        if MethodExtractor.is_member_variable(raw_line):
            var_name = MethodExtractor.extract_variable_name(raw_line)
            # Attempt to extract data type (the token before the variable name)
            # e.g. "private List<String> items;" → data_type "List<String>"
            left = raw_line.strip().split("=")[0].strip().rstrip(";")
            tokens = left.split()
            data_type = tokens[-2] if len(tokens) >= 2 else None
            context.append({
                "element_name": var_name,
                "type": "member_variable",
                "start_line": idx + 1,
                "end_line": idx + 1,
                "extends": [],
                "implements": [],
                "return_type": None,
                "parameters": [],
                "data_type": data_type
            })
            continue

        # 4) class / interface / enum / method declarations
        if MethodExtractor.is_function_line(raw_line):
            # Determine if it's a class/interface/enum or a method
            decl_type = None
            if re.search(r'\bclass\b', stripped):
                decl_type = "class"
            elif re.search(r'\binterface\b', stripped):
                decl_type = "interface"
            elif re.search(r'\benum\b', stripped):
                decl_type = "enum"
            else:
                decl_type = "method"

            elem_name = MethodExtractor.extract_method_name(raw_line)

            # Find the body end line (brace matching)
            # Start searching at this line, column 0 (first '{' will be found)
            end_line = MethodExtractor.find_body_end(idx, 0, lines) or (idx + 1)

            # Initialize defaults
            extends_list = []
            implements_list = []
            return_type = None
            parameters: List[tuple] = []
            data_type = None

            # Parse extends/implements for class/interface/enum
            if decl_type in ("class", "interface", "enum"):
                # Regex to capture name, optional extends, optional implements
                # Examples:
                #   "public class MyClass extends BaseClass implements Runnable, Serializable {"
                #   "interface MyInterface extends Serializable {"
                type_pattern = r'\b(?:class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)' + \
                               r'(?:\s+extends\s+([A-Za-z0-9_<>\.,\s]+))?' + \
                               r'(?:\s+implements\s+([A-Za-z0-9_<>\.,\s]+))?'
                m = re.search(type_pattern, stripped)
                if m:
                    elem_name = m.group(1)
                    if m.group(2):
                        # Split on commas and strip whitespace
                        extends_list = [e.strip() for e in m.group(2).split(",")]
                    if m.group(3):
                        implements_list = [i.strip() for i in m.group(3).split(",")]

                context.append({
                    "element_name": elem_name,
                    "type": decl_type,
                    "start_line": idx + 1,
                    "end_line": end_line,
                    "extends": extends_list,
                    "implements": implements_list,
                    "return_type": None,
                    "parameters": [],
                    "data_type": None
                })
            else:
                # It's a method or constructor
                # Attempt to extract return type and parameters
                # Example: "public void doWork(String input, int count) {"
                method_regex = r'^(?:public|protected|private|\s)*(?:static\s+)?([A-Za-z_<>\[\]]+)\s+' + \
                               r'([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)'
                m2 = re.search(method_regex, stripped)
                if m2:
                    return_type = m2.group(1).strip()
                    # Method name already in elem_name
                    params_str = m2.group(3).strip()
                    if params_str:
                        # Split on commas, then split each param by whitespace
                        for param in params_str.split(","):
                            parts = param.strip().split()
                            if len(parts) >= 2:
                                param_type = " ".join(parts[:-1])
                                param_name = parts[-1]
                                parameters.append((param_type, param_name))

                context.append({
                    "element_name": elem_name,
                    "type": "method",
                    "start_line": idx + 1,
                    "end_line": end_line,
                    "extends": [],
                    "implements": [],
                    "return_type": return_type,
                    "parameters": parameters,
                    "data_type": None
                })

            continue

    return context
