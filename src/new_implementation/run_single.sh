proj="Lang"
bug=56
python3.9 src/new_implementation/driver.py --diff-file /root/data/Defects4J/project_diffs/${proj}_${bug}.diff --repo-root /tmp/repos/${proj}_${bug}/ --out-csv /root/data/Defects4J/relevent_gt_data/${proj}_${bug}.csv
