import os
import argparse
from diff_processor import DiffProcessor
import logging

# Configure root logger to show debug/warning messages from diff_processor
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
logger = logging.getLogger("diff_processor")

if __name__ == "__main__":
    """
    Example usage:
      python driver.py \
        --diff-file /root/data/Defects4J/project_diffs/Foo_123.diff \
        --repo-root /home/irtaza11/myproject \
        --out-csv changes.csv
    """
    parser = argparse.ArgumentParser(
        description="Parse a unified diff and output a DataFrame of changed Java elements."
    )
    parser.add_argument(
        "--diff-file",
        required=True,
        help="Path to a unified diff (e.g. output of `git show --no-prefix -U500`)."
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help="Path to the Git checkout root (so we can open original .java files)."
    )
    parser.add_argument(
        "--out-csv",
        required=False,
        help="Optional: write the resulting DataFrame to this CSV file."
    )
    args = parser.parse_args()

    if not os.path.isfile(args.diff_file):
        logger.error(f"Cannot find diff file: {args.diff_file}")
        exit(1)
    if not os.path.isdir(args.repo_root):
        logger.error(f"Cannot find repo root directory: {args.repo_root}")
        exit(1)

    with open(args.diff_file, "r", encoding="utf-8") as f:
        diff_text = f.read()

    processor = DiffProcessor(repo_root=args.repo_root)
    df = processor.parse_diff_to_dataframe(diff_text)

    # Optionally write CSV
    if args.out_csv:
        try:
            df.to_csv(args.out_csv, index=False)
            logger.info(f"Wrote full results to CSV: {args.out_csv}")
        except Exception as e:
            logger.error(f"Failed to write CSV to {args.out_csv}: {e}")
