#!/bin/bash
# SlideMakr - Cloud Run Deployment Script
#
# Usage: ./deploy.sh
#
# Prerequisites:
# - gcloud CLI installed and authenticated
# - GOOGLE_CLOUD_PROJECT set in environment
# - Service account JSON accessible

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-slidemakr}"
SERVICE_NAME="slidemakr"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=== SlideMakr Cloud Run Deployment ==="
echo "Project: ${PROJECT_ID}"
echo "Service: ${SERVICE_NAME}"
echo "Region:  ${REGION}"
echo ""

# Set project
gcloud config set project "${PROJECT_ID}"

# Build container image
echo "[1/4] Building container image..."
gcloud builds submit --tag "${IMAGE}" .

# Deploy to Cloud Run
echo "[2/4] Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE}" \
    --region "${REGION}" \
    --platform managed \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 1 \
    --max-instances 5 \
    --timeout 300 \
    --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
    --update-secrets "GOOGLE_API_KEY=GOOGLE_API_KEY:latest,SERVICE_ACCOUNT_JSON=SERVICE_ACCOUNT_JSON:latest"

# Get the service URL
echo "[3/4] Getting service URL..."
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format 'value(status.url)')

echo ""
echo "[4/4] Deployment complete!"
echo "==================================="
echo "Service URL: ${SERVICE_URL}"
echo "==================================="
