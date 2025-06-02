#!/bin/bash

REPO_DIR="/tmp/repos/"
SCRIPT="src/checkout-gitshow-hashing/main.py"

# Updated list of (Project, BugID) pairs
PROJECT_BUG_LIST=(
    "JacksonDatabind 44"
    "Closure 35"
    "Closure 176"
    "Jsoup 61"
    "JacksonDatabind 26"
    "Closure 6"
    "Closure 172"
    "Collections 27"
    "JacksonDatabind 37"
    "Math 12"
    "Cli 36"
    "JacksonDatabind 89"
    "JacksonDatabind 92"
    "Mockito 8"
    "Lang 41"
    "Chart 23"
)

# Loop through the list and run the Python script
for entry in "${PROJECT_BUG_LIST[@]}"; do
    read -r PROJECT BUG_NUM <<< "$entry"
    
    echo "Running for $PROJECT bug #$BUG_NUM"
    python3.9 "$SCRIPT" --project "$PROJECT" --bug_num "$BUG_NUM"
done
