#!/bin/ash

# Ensure youtube-dl is always up-to-date when restarting pod
pip install --upgrade youtube-dl

./jeeves.py