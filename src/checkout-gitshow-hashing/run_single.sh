#!/bin/bash

PROJECT="Jsoup"
BUG_NUM=25
REPO_DIR="/tmp/repos/"

python3.9 src/checkout-gitshow-hashing/main.py --project $PROJECT --bug_num $BUG_NUM
