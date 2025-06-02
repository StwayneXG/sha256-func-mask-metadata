#!/bin/bash

PROJECT="JacksonDatabind"
BUG_NUM=44
REPO_DIR="/tmp/repos/"

python3.9 src/checkout-gitshow-hashing/main.py --project $PROJECT --bug_num $BUG_NUM
