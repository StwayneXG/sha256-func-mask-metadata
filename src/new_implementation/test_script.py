import os
import pandas as pd

def validate_diff_consistency(folder_path):
    removal_only_log = []
    missing_line_errors = []

    for file in os.listdir(folder_path):
        if not file.endswith(".csv"):
            continue

        full_path = os.path.join(folder_path, file)
        df = pd.read_csv(full_path)

        project_bug = os.path.splitext(file)[0]  # Strip extension
        if "_" not in project_bug:
            print(f"Skipping malformed filename: {file}")
            continue

        for idx, row in df.iterrows():
            element_source = row.get("element_source")
            diff_lines = row.get("diff_lines")

            # Skip if missing data
            if pd.isna(diff_lines) or pd.isna(element_source):
                continue

            # Convert diff_lines from stringified list to actual list if needed
            if isinstance(diff_lines, str) and diff_lines.startswith("["):
                try:
                    import ast
                    diff_lines = ast.literal_eval(diff_lines)
                except Exception as e:
                    raise ValueError(f"Could not parse diff_lines at row {idx} in {file}: {e}")

            additions = [line[1:].strip() for line in diff_lines if line.startswith("+")]
            removals_only = all(line.startswith("-") for line in diff_lines)

            if element_source == "NA":
                continue  # skip removed-only methods

            element_lines = [line.strip() for line in element_source.split("\n")]

            if removals_only:
                removal_only_log.append((project_bug, idx))
                continue

            for add_line in additions:
                if add_line not in element_lines:
                    missing_line_errors.append((project_bug, idx, add_line))

    # Output summary
    if removal_only_log:
        print("\n⚠️ Removal-only cases (no element_source validation):")
        for proj_bug, idx in removal_only_log:
            print(f" - {proj_bug} [row {idx}]")

    if missing_line_errors:
        print("\n❌ Missing added lines in element_source:")
        for proj_bug, idx, line in missing_line_errors:
            print(f" - {proj_bug} [row {idx}]: Missing line -> {line}")
        raise AssertionError(f"{len(missing_line_errors)} mismatches found in element_source content.")
    else:
        print("\n✅ All added lines matched successfully!")

# Example usage
validate_diff_consistency("/root/data/Defects4J/relevent_gt_data/")
