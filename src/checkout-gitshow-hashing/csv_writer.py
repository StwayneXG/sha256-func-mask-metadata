import os
import pandas as pd

from config import logging_level
from logging_utils import get_console_logger
script_logger = get_console_logger(__name__, level=logging_level)

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
        if os.path.exists(filename):
            existing_df = pd.read_csv(filename)

            # Sort both DataFrames by all columns to ignore row order
            df_sorted = df.sort_values(by=df.columns.tolist()).reset_index(drop=True)
            existing_df_sorted = existing_df.sort_values(by=existing_df.columns.tolist()).reset_index(drop=True)

            if not existing_df_sorted.equals(df_sorted):
                script_logger.info(f"Updating {filename} as contents differ.")
                script_logger.info("Existing DataFrame:")
                script_logger.info(f"\n{existing_df_sorted}")
                script_logger.info("Current DataFrame:")
                script_logger.info(f"\n{df_sorted}")
            else:
                script_logger.info(f"Both df are equal for {filename}")

        # df.to_csv(filename, index=False)
        script_logger.debug(f"CSV written to {filename} with {len(rows)} rows")