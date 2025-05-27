#!/bin/bash

D4J_HOME="$(dirname $(which defects4j))/../.."
REPO_DIR="/tmp/repos/"

for proj in $(defects4j pids); do
    for bug in $(cut -f1 -d',' "$D4J_HOME/framework/projects/$proj/commit-db"); do
        python3.9 src/checkout-gitshow-hashing/main.py --project $proj --bug_num $bug
        # rm -rf $REPO_DIR/${proj}_${bug}
    done
done
