import csv
from pathlib import Path

class CSVWriter:
    def __init__(self, file_path):
        self.file_path = Path(file_path)

    def write_csv(self, data):
        with self.file_path.open('w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Old Method Name", "New Method Name"])
            for row in data:
                writer.writerow(row)