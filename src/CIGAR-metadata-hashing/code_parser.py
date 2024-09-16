import javalang

class CodeParser:
    @staticmethod
    def parse_code(code):
        wrapped_code = f"public class WrappedCode {{\n{code}\n}}"
        try:
            return javalang.parse.parse(wrapped_code)
        except javalang.parser.JavaSyntaxError:
            # print(f"Failed to parse code:\n{code}")
            return None

    @staticmethod
    def extract_method_names(tree):
        method_names = list()
        for _, node in tree.filter(javalang.tree.MethodDeclaration):
            method_names.append(node.name)
        for _, node in tree.filter(javalang.tree.ConstructorDeclaration):
            method_names.append(node.name)
        
        if len(method_names) == 0:
            return []
        return [method_names[0]]