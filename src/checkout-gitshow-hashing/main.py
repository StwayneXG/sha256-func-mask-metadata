from project_checkout import ProjectCheckout
from git_diff_parser import GitDiffParser
from method_extractor import MethodExtractor
from method_name_hasher import MethodNameHasher
from csv_writer import CSVWriter

class MainProcessor:
    def __init__(self, base_dir, output_dir):
        self.project_checkout = ProjectCheckout(base_dir)
        self.csv_writer = CSVWriter(output_dir)

    def process(self, project, bug_num):
        project_dir = self.project_checkout.checkout(project, bug_num)
        diff = GitDiffParser.get_diff(project_dir)
        methods = MethodExtractor.extract_methods(diff)
        
        method_pairs = []
        for method_name in methods:
            hashed_name = MethodNameHasher.hash_method_name(method_name)
            method_pairs.append((method_name, hashed_name))

        self.csv_writer.write_csv(project, bug_num, method_pairs)

if __name__ == "__main__":
    base_dir = "/root/data/Defects4J/repos/"
    output_dir = "data/sha256 mask checkout-gitshow/"
    
    processor = MainProcessor(base_dir, output_dir)
    
    # Example usage:
    processor.process("Chart", "1")
    processor.process("Time", "2")
    # Add more projects and bug numbers as needed