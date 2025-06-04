#!/bin/bash

PROJECT="Chart"
BUG_NUM=1
REPO_DIR="/tmp/repos/"

python3.9 src/checkout-gitshow-hashing/main.py --project $PROJECT --bug_num $BUG_NUM
