@echo off
cd /d "C:\Users\Jackb\Downloads\New folder (2)"
python -u scripts\jiji_incremental.py --slugs-file data\category_tree.tsv --max-pages 3 --sort new >> data\logs\live.log 2>&1
