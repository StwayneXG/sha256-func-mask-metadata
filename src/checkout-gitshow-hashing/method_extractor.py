import re

class MethodExtractor:
    @staticmethod
    def is_function_line(line):
        trimmed_line = line.strip()
        if trimmed_line.startswith("//"):
            return False
        
        if "(" in trimmed_line and any(keyword in trimmed_line for keyword in ["private", "protected", "public", "static", "void"]):
            return True
        elif "JSType" in trimmed_line and "{" in trimmed_line:
            return True
        elif "class" in trimmed_line and "{" in trimmed_line:
            return True
        
        return False

    @staticmethod
    def extract_method_name(line):
        # This regex pattern matches the method name in various declaration formats
        pattern = r'(?:(?:public|private|protected|static|final|native|synchronized|abstract|transient)+\s+)+[$_\w<>\[\]\s]*\s+(\w+)\s*\('
        match = re.search(pattern, line)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def extract_methods(diff):
        lines = diff.split('\n')
        methods = []

        for i, line in enumerate(lines):
            if line.startswith('+') or line.startswith('-'):
                # Look upwards for the function declaration
                for j in range(i, -1, -1):
                    if MethodExtractor.is_function_line(lines[j]):
                        method_name = MethodExtractor.extract_method_name(lines[j])
                        if method_name:
                            methods.append(method_name)
                        break  # Stop looking once we've found the function declaration

        return methods