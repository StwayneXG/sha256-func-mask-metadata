from project_checkout import ProjectCheckout
from git_diff_parser import GitDiffParser
from method_extractor import MethodExtractor
from method_name_hasher import MethodNameHasher
from csv_writer import CSVWriter
import argparse
import os

def parse_args():
    parser = argparse.ArgumentParser(description="Process a project")
    parser.add_argument("--project", help="The project to process")
    parser.add_argument("--bug_num", help="The bug number to process")
    return parser.parse_args()

class MainProcessor:
    def __init__(self, base_dir, output_dir):
        self.project_checkout = ProjectCheckout(base_dir)
        self.csv_writer = CSVWriter(output_dir)

    def process(self, project, bug_num):
        diff_file_path = f"/root/data/Defects4J/project_diffs/{project}_{bug_num}.diff"
        os.makedirs(os.path.dirname(diff_file_path), exist_ok=True)
        

        if not os.path.exists(diff_file_path):
            project_dir = self.project_checkout.checkout(project, bug_num)
            diff = GitDiffParser.get_diff(project_dir)

            # Save diff to folder "/root/data/Defects4J/project_diffs/{project}_{bug_num}.txt"
            with open(diff_file_path, 'w') as diff_file:
                diff_file.write(diff)
        else:
            with open(diff_file_path, 'r') as diff_file:
                diff = diff_file.read()
        
        methods = MethodExtractor.extract_methods(diff)
        base_path = f'/tmp/repos/{project}_{bug_num}/'
        method_implementations = MethodExtractor.extract_method_implementations(diff, methods, base_path)

        for method_name, implementation in method_implementations.items():
            print(f"Method: {method_name}\nImplementation:\n{implementation}")

        # method_triples = []
        # for file_path, method_name_line_pairs in methods.items():
        #     for method_name, method_line in method_name_line_pairs:
        #         hashed_name = MethodNameHasher.hash_method_name(method_name)
        #         implementation = method_implementations.get(method_name, "")
        #         method_triples.append((method_name, f"func_{hashed_name}", implementation))

        # self.csv_writer.write_csv(project, bug_num, method_triples)

if __name__ == "__main__":
    base_dir = "/tmp/repos/"
    os.makedirs(base_dir, exist_ok=True)
    output_dir = "data/sha256 mask checkout-gitshow/"
    
    processor = MainProcessor(base_dir, output_dir)

    args = parse_args()
    processor.process(args.project, args.bug_num)   