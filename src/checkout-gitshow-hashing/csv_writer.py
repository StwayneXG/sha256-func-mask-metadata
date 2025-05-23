import os
import pandas as pd

class CSVWriter:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def write_csv(self, project, bug_num, method_triples):
        os.makedirs(self.output_dir, exist_ok=True)
        filename = os.path.join(self.output_dir, f"{project}_{bug_num}.csv")
        df = pd.DataFrame(method_triples, columns=["Old Method Name", "New Method Name", "Method Implementation"])
        df.to_csv(filename, index=False)