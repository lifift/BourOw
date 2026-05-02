#!/bin/bash
# ============================================================
# deploy.sh — Déploiement sur GCP Cloud Functions
# Exécuter UNE SEULE FOIS pour tout installer.
# ============================================================

set -e

# ── À RENSEIGNER ──────────────────────────────────────────
PROJECT_ID="xxx"       # project ID GCP
REGION="europe-west1"
GMAIL_USER="xxxxxx@gmail.com"
GMAIL_PASSWORD="xxxxx"   # Mot de passe d'appli Gmail (16 car.)
RECIPIENT_EMAIL="xxxxx@gmail.com"
GEMINI_API_KEY="xxxxxx"              # Depuis https://aistudio.google.com
NEWSAPI_KEY="xxxxxxxx"    # Depuis https://newsapi.org/register (gratuit)
# ─────────────────────────────────────────────

gcloud config set project $PROJECT_ID
gcloud services enable cloudfunctions.googleapis.com cloudscheduler.googleapis.com cloudbuild.googleapis.com

SECRETS="GMAIL_USER=$GMAIL_USER,GMAIL_PASSWORD=$GMAIL_PASSWORD,RECIPIENT_EMAIL=$RECIPIENT_EMAIL,GEMINI_API_KEY=$GEMINI_API_KEY,NEWSAPI_KEY=$NEWSAPI_KEY"

deploy_fn() {
  gcloud functions deploy $1 \
    --gen2 --runtime=python312 --region=$REGION --source=. \
    --entry-point=$1 --trigger=http --allow-unauthenticated \
    --memory=256MB --timeout=120s --set-env-vars="$SECRETS"
}

deploy_fn weekly_report
deploy_fn alert_monitor

WEEKLY_URL=$(gcloud functions describe weekly_report --region=$REGION --gen2 --format="value(serviceConfig.uri)")
ALERT_URL=$(gcloud functions describe alert_monitor --region=$REGION --gen2 --format="value(serviceConfig.uri)")

gcloud scheduler jobs create http weekly-report-job \
  --location=$REGION --schedule="0 8 * * 1" --uri="$WEEKLY_URL" \
  --http-method=GET --time-zone="Europe/Paris" 2>/dev/null \
  || gcloud scheduler jobs update http weekly-report-job \
       --location=$REGION --schedule="0 8 * * 1" --uri="$WEEKLY_URL"

gcloud scheduler jobs create http alert-monitor-job \
  --location=$REGION --schedule="0 */4 * * *" --uri="$ALERT_URL" \
  --http-method=GET --time-zone="Europe/Paris" 2>/dev/null \
  || gcloud scheduler jobs update http alert-monitor-job \
       --location=$REGION --schedule="0 */4 * * *" --uri="$ALERT_URL"

echo ""
echo "✅ DÉPLOIEMENT TERMINÉ !"
echo "Pour tester immédiatement :"
echo "  gcloud scheduler jobs run weekly-report-job --location=$REGION"
echo "  gcloud scheduler jobs run alert-monitor-job --location=$REGION"
