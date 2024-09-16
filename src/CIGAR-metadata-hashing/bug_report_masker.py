import pandas as pd
from pathlib import Path

class BugReportMasker:
    def __init__(self, bugreports_file, sha256_dir, output_file):
        self.bugreports_file = Path(bugreports_file)
        self.sha256_dir = Path(sha256_dir)
        self.output_file = Path(output_file)

    def mask_bug_reports(self):
        # Read the original bug reports
        df = pd.read_csv(self.bugreports_file)

        # Filter rows based on existing CSV files
        df['CSV_Exists'] = df.apply(lambda row: self._csv_exists(row['Project'], row['Bug Number']), axis=1)
        df = df[df['CSV_Exists']].drop('CSV_Exists', axis=1)

        # Apply masking to Heading and Description
        df['Masked_Heading'] = df.apply(lambda row: self._mask_text(row['Heading'], row['Project'], row['Bug Number']), axis=1)
        df['Masked_Description'] = df.apply(lambda row: self._mask_text(row['Description'], row['Project'], row['Bug Number']), axis=1)

        # Save the masked dataframe
        df[['Project', 'Bug Number', 'Masked_Heading', 'Masked_Description']].to_csv(self.output_file, index=False)
        print(f"Masked bug reports saved to {self.output_file} with {len(df)} rows")

    def _csv_exists(self, project, bug_number):
        return (self.sha256_dir / f"{project}-{bug_number}.csv").exists()

    def _mask_text(self, text, project, bug_number):
        if pd.isna(text):
            return text
        
        mask_file = self.sha256_dir / f"{project}-{bug_number}.csv"
        masks = pd.read_csv(mask_file)
        
        for _, row in masks.iterrows():
            old_name = row['Old Method Name']
            new_name = row['New Method Name']
            text = text.replace(old_name, new_name)
        
        return text
