#!/usr/bin/env bash
# Build the submission packages in dist/:
#   LaunchReady_source.zip  full source incl. Esko's sample files (HackerEarth "Source Code" upload)
#   LaunchReady_github.zip  same, without Esko's sample files (for a public GitHub repository)
#   LaunchReady_extras.zip  write-up PDF, demo video, architecture diagram, submission text
# Never includes caches, runs, .env or generated evaluation artworks (tools/evaluate.py re-creates those).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist

build() {  # $1 = zip name, $2 = "public" to leave out Esko's files
  local name=$1 mode=${2:-full}
  local stage; stage=$(mktemp -d)
  local dest="$stage/LaunchReady"
  mkdir -p "$dest"
  tar --exclude='./.cache' --exclude='./.runs' --exclude='./.env' --exclude='./dist' --exclude='./.venv' \
      --exclude='*/__pycache__' --exclude='./.pytest_cache' --exclude='./data/eval/*/*' \
      -cf - . | tar -xf - -C "$dest"
  find "$dest/data/eval" -mindepth 1 -type d -empty -delete
  if [ "$mode" = public ]; then
    find "$dest/data/samples/esko_granola" -type f ! -name README.md -delete
  fi
  if grep -rqsE "AQ\.[A-Za-z0-9_-]{30,}|AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}" "$dest"; then
    echo "refusing to package: something that looks like an API key was found" >&2; exit 1
  fi
  rm -f "dist/$name"
  (cd "$stage" && zip -qr9 - LaunchReady) > "dist/$name"
  rm -rf "$stage"
}

build LaunchReady_source.zip full
build LaunchReady_github.zip public

rm -f dist/LaunchReady_extras.zip
zip -qj dist/LaunchReady_extras.zip dist/LaunchReady_submission.pdf dist/LaunchReady_demo.mp4 \
    docs/architecture.png docs/SUBMISSION_TEXT.md
ls -la dist
