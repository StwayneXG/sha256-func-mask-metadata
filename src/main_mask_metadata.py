from pathlib import Path
from json_processor import JSONProcessor

def process_json_files(input_dir, output_dir):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for json_file in input_path.glob('*.json'):
        processor = JSONProcessor(json_file, output_path)
        processor.process()

def main():
    input_dir = "data/CIGAR metadata"
    output_dir = "data/sha256 mask metadata"
    process_json_files(input_dir, output_dir)

if __name__ == "__main__":
    main()