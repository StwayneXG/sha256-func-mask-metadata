import json
from pathlib import Path

class JSONReader:
    def __init__(self, file_path):
        self.file_path = Path(file_path)

    def read_json(self):
        with self.file_path.open('r') as file:
            return json.load(file)
