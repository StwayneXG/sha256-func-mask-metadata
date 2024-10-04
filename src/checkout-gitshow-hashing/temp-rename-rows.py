import os
import pandas as pd

def main():
    mask_dir = "data/sha256 mask checkout-gitshow/"
    output_dir = "data/sha256 mask checkout-gitshow renamed/"

    os.makedirs(output_dir, exist_ok=True)    
    mask_files = os.listdir(mask_dir)

    for mask_file in mask_files:
        mask_path = os.path.join(mask_dir, mask_file)
        df = pd.read_csv(mask_path)

        # Rename rows: "Old Method Name" -> "Original Function Name", "New Method Name" -> "Hash Mask Name", "Method Implementation" -> "Function Code"
        df.rename(columns={"Old Method Name": "Original Function Name", "New Method Name": "Hash Mask Name", "Method Implementation": "Function Code"}, inplace=True)
        
        # Reorder columns to "Original Function Name", "Function Code", "Hash Mask Name"
        df = df[["Original Function Name", "Function Code", "Hash Mask Name"]]

        # Write to CSV
        output_path = os.path.join(output_dir, mask_file)
        df.to_csv(output_path, index=False)

if __name__ == "__main__":
    main()