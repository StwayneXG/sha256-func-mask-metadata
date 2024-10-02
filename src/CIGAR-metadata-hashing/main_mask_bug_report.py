
import os
from pathlib import Path
from json_processor import JSONProcessor
from bug_report_masker import BugReportMasker

def process_json_files(input_dir, output_dir):
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for json_file in input_path.glob('*.json'):
        processor = JSONProcessor(json_file, output_path)
        processor.process()

def main():

    # Add the bug report masking step
    bugreports_file = "data/defects4j/bugreports.csv"
    masked_output = "data/defects4j/masked_bugreports.csv"
    mask_data_dir = "data/sha256 mask checkout-gitshow"
    masker = BugReportMasker(bugreports_file, mask_data_dir, masked_output)
    masker.mask_bug_reports()

if __name__ == "__main__":
    main()