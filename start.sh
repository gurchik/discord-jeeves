#!/bin/ash

# Ensure yt-dlp is always up-to-date when restarting pod
echo "Upgrading yt-dlp"
pip install --upgrade yt-dlp

echo "Starting jeeves"
./jeeves.py