#!/bin/bash
# ============================================================
# Google Service Account Setup for PLUVIO
# ============================================================
#
# This script guides you through setting up Google Drive/Sheets
# API access for reading inventory Excel files.
#
# Prerequisites:
#   - A Google account
#   - Access to Google Cloud Console
#
# ============================================================

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Google Service Account Setup for PLUVIO            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

echo "📋 STEP 1: Create Google Cloud Project"
echo "   → Go to: https://console.cloud.google.com/"
echo "   → Click 'Select a project' → 'New Project'"
echo "   → Name: 'pluvio-sensus' → Click 'Create'"
echo ""

echo "📋 STEP 2: Enable APIs"
echo "   → Go to: https://console.cloud.google.com/apis/library/sheets.googleapis.com"
echo "   → Click 'Enable'"
echo "   → Go to: https://console.cloud.google.com/apis/library/drive.googleapis.com"
echo "   → Click 'Enable'"
echo ""

echo "📋 STEP 3: Create Service Account"
echo "   → Go to: https://console.cloud.google.com/iam-admin/serviceaccounts"
echo "   → Click 'Create Service Account'"
echo "   → Name: 'pluvio-reader'"
echo "   → Click 'Create and Continue'"
echo "   → (Skip roles) Click 'Done'"
echo ""

echo "📋 STEP 4: Create & Download Key"
echo "   → Click on the service account you just created"
echo "   → Go to 'Keys' tab → 'Add Key' → 'Create new key'"
echo "   → Choose 'JSON' → Click 'Create'"
echo "   → Save the downloaded JSON file"
echo ""

echo "📋 STEP 5: Share Google Sheet & Drive Folder"
echo "   → Share your Google Sheet with the service account email"
echo "     (found in the JSON file under 'client_email')"
echo "   → Share the Google Drive folder with the same email"
echo "   → Set permission to 'Viewer'"
echo ""

echo "📋 STEP 6: Configure Environment Variables"
echo ""
echo "   Option A: Save JSON file locally"
echo "   → Save the JSON as 'gdrive-creds.json' in this project root"
echo ""
echo "   Option B: Add to Vercel"
echo "   → Copy the entire JSON content"
echo "   → Go to Vercel Dashboard → pluvio → Settings → Environment Variables"
echo "   → Add: GDRIVE_CREDS_JSON = <paste entire JSON>"
echo ""
echo "   Option C (optional): Set inventory spreadsheet ID"
echo "   → If you've converted the Excel to a Google Sheet:"
echo "   → Add: INVENTORY_SPREADSHEET_ID = <sheet-id from URL>"
echo ""

echo "📋 STEP 7: Test"
echo "   → Run: python3 -c 'from main import read_inventory; print(read_inventory())'"
echo ""

echo "✅ Setup complete! Deploy with: vercel --prod --yes"
echo ""
