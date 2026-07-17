#!/bin/bash
cd /app
git apply /tests/solution.patch
git add -A
git commit -m "Add alias support"
