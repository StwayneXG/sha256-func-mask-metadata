import os
import pandas as pd

class CSVWriter:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def write_csv(self, project, bug_num, methods_info):
        os.makedirs(self.output_dir, exist_ok=True)
        filename = os.path.join(self.output_dir, f"{project}_{bug_num}.csv")

        rows = []
        for file_path, method_map in methods_info.items():
            for (method_name, line_number), implementation in method_map.items():
                rows.append([file_path, method_name, line_number, implementation])

        df = pd.DataFrame(rows, columns=["File Path", "Method Name", "Method Line Number", "Method Implementation"])
        df.to_csv(filename, index=False)
