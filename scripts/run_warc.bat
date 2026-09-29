@echo off
cd /d "C:\Users\Jackb\Downloads\New folder (2)"
python -u scripts\warc_fetch.py --max 2000 --delay 0.8 >> data\logs\warc.log 2>&1
