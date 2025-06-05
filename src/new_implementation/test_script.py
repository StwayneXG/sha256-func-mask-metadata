import os
import pandas as pd

def validate_diff_consistency(folder_path):
    for file in os.listdir(folder_path):
        if not file.endswith(".csv"):
            continue

        full_path = os.path.join(folder_path, file)
        df = pd.read_csv(full_path)

        project_bug = os.path.splitext(file)[0]  # Strip extension
        print(f"analyzing {project_bug}")
        if "_" not in project_bug:
            print(f"Skipping malformed filename: {file}")
            continue

        for idx, row in df.iterrows():
            element_source = row["element_source"]
            # Skip if missing data
            if pd.isna(element_source):
                if row['change_type'] == "removed":
                    continue
                else:
                    raise ValueError(f"source empty but not removal for {project_bug}")
            element_source_lines = element_source.split('\n')
            
            diff_lines = row.get("diff_lines")
            diff_lines = eval(diff_lines)


            removals_only = True
            # Convert diff_lines from stringified list to actual list if needed
            for diff_line in diff_lines:
                if diff_line.startswith('+'):
                    removals_only = False
                    if not diff_line[1:] in element_source_lines:
                        raise ValueError(f"Diff line {diff_line} not found for {project_bug} in source\n{element_source}")
            
            if removals_only:
                print(f"removal only for {project_bug} for method {row['element_name']}")

# Example usage
validate_diff_consistency("/root/data/Defects4J/relevent_gt_data/")
