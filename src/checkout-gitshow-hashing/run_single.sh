#!/bin/bash

PROJECT="Math"
BUG_NUM=71
REPO_DIR="/tmp/repos/"

python3.9 src/checkout-gitshow-hashing/main.py --project $PROJECT --bug_num $BUG_NUM
