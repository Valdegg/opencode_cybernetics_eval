#!/bin/bash
cd /app
# Reference solution: apply the solution patch
git apply /tests/solution.patch
git add -A
git commit -m "Add alias support to name_mapping"
