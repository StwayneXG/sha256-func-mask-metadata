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
        methods = set()

        for i, line in enumerate(lines):
            if line.startswith('+') or line.startswith('-'):
                # Look upwards for the function declaration
                for j in range(i, -1, -1):
                    if MethodExtractor.is_function_line(lines[j]):
                        method_name = MethodExtractor.extract_method_name(lines[j])
                        if method_name:
                            methods.add(method_name)
                        break  # Stop looking once we've found the function declaration

        return methods