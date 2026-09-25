#!/bin/bash

echo "Analyzing chunk distribution..."
echo ""

# File 1: 26.Luat-BHYT-2008.jsonl
echo "===== File: 26.Luat-BHYT-2008.jsonl ====="
FILE="data/processed/26.Luat-BHYT-2008.jsonl"
LINES=$(wc -l < "$FILE")
echo "Total chunks: $LINES"

# Extract text field lengths and get stats
echo "Extracting chunk lengths..."
cat "$FILE" | sed -n 's/.*"text":"\(.*\)","doc_group.*/\1/p' | wc -c > /tmp/lens1.txt
head -1 "$FILE" | sed -n 's/.*"text":"\(.*\)","doc_group.*/\1/p' | wc -c

echo ""
echo "===== File: 29.Luat-Nghia-vu-quan-su-2015-QH13.jsonl ====="
FILE2="data/processed/29.Luat-Nghia-vu-quan-su-2015-QH13.jsonl"
LINES2=$(wc -l < "$FILE2")
echo "Total chunks: $LINES2"

echo ""
echo "===== File: 34.Luat-Cu-tru-2020.jsonl ====="
FILE3="data/processed/34.Luat-Cu-tru-2020.jsonl"
LINES3=$(wc -l < "$FILE3")
echo "Total chunks: $LINES3"

