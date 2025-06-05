import re

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
        # Detect class or enum declaration
        if ("class " in trimmed or " enum " in trimmed or trimmed.startswith("class ") or trimmed.startswith("enum ")):
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
        # Case 1: class or enum
        m_class = re.search(r'\b(class|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', trimmed)
        if m_class:
            return m_class.group(2)
        # Case 2: method or constructor (name before '('
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
