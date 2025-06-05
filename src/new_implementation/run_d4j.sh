#!/bin/bash

D4J_HOME="$(dirname $(which defects4j))/../.."
REPO_DIR="/tmp/repos/"

for proj in $(defects4j pids); do
    for bug in $(cut -f1 -d',' "$D4J_HOME/framework/projects/$proj/commit-db"); do
        python3.9 src/new_implementation/driver.py --diff-file /root/data/Defects4J/project_diffs/${proj}_${bug}.diff --repo-root /tmp/repos/${proj}_${bug}/ --out-csv /root/data/Defects4J/relevent_gt_data/${proj}_${bug}.csv
        # python3.9 src/checkout-gitshow-hashing/main.py --project $proj --bug_num $bug
        # rm -rf $REPO_DIR/${proj}_${bug}
    done
done
