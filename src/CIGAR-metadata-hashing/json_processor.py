from json_reader import JSONReader
from code_parser import CodeParser
from method_hasher import MethodHasher
from csv_writer import CSVWriter
from pathlib import Path

class JSONProcessor:
    def __init__(self, input_file, output_dir):
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.json_reader = JSONReader(self.input_file)
        self.code_parser = CodeParser()
        self.method_hasher = MethodHasher()
        self.csv_writer = CSVWriter(self.output_dir / f"{self.input_file.stem}.csv")

    def process(self):
        json_data = self.json_reader.read_json()
        
        if json_data.get("bug_type") != "OT":
            code = json_data.get("code", "")
            if code:
                tree = self.code_parser.parse_code(code)
                if not tree:
                    print(f"Skipped {self.input_file.name} due to parsing error")
                    return
                method_names = self.code_parser.extract_method_names(tree)

                if len(method_names) == 0:
                    print(f"No methods found in {self.input_file.name}")
                    return

                hashed_methods = [
                    (name, f"func_{self.method_hasher.hash_method_name(name)}")
                    for name in method_names
                ]
                
                self.csv_writer.write_csv(hashed_methods)
                print(f"Processed {self.input_file.name} and saved results to {self.csv_writer.file_path.name}")
            else:
                print(f"No code found in {self.input_file.name}")
        else:
            print(f"Skipped {self.input_file.name} due to bug_type 'OT'")