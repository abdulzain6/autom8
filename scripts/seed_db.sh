#!/bin/bash


seed_all_apps() {
  # Seed the database with Apps
  for app_dir in ./apps/*/; do
    app_file="${app_dir}app.json"
    python -m aci.cli upsert-app \
      --app-file "$app_file" \
      --skip-dry-run
  done

  # Seed the database with Functions
  for functions_file in ./apps/*/functions.json; do
    python -m aci.cli upsert-functions \
      --functions-file "$functions_file" \
      --skip-dry-run
  done
}


seed_all_apps
