#!/usr/bin/env bash
# Native Debian/Ubuntu hosts need the same OpenCV libraries as our Docker image.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgl1 libglib2.0-0
