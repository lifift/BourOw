# Agent IA — Suivi news boursières

Deux fonctionnalités :
1. **Rapport hebdomadaire** (lundi 8h) : 1-3 bullet points par entreprise
2. **Alertes critiques** (toutes les 4h) : email immédiat si news grave détectée

Stack : GCP Cloud Functions + Cloud Scheduler + Google News RSS + Gemini 1.5 Flash + Gmail

**Coût estimé : 0 €/mois** (tiers gratuits suffisants)

---

## Prérequis

- Un compte Google / Gmail
- Un projet GCP (gratuit à créer sur console.cloud.google.com)
- `gcloud` CLI installé localement (`brew install google-cloud-sdk` ou équivalent)

---

## 1. Créer un mot de passe d'application Gmail

Ton mot de passe Gmail normal ne fonctionne pas avec SMTP.
Il faut créer un **mot de passe d'application** :

1. Aller sur https://myaccount.google.com/security
2. Activer la **Validation en 2 étapes** (obligatoire)
3. Chercher "Mots de passe des applications"
4. Créer une application → type "Mail" → copier le code 16 caractères

---

## 2. Obtenir une clé API Gemini (gratuit)

1. Aller sur https://aistudio.google.com/app/apikey
2. Cliquer "Create API Key"
3. Copier la clé (commence par `AIza...`)

Le tier gratuit offre : 15 req/min, 1 500 req/jour, 1M tokens/jour.
Largement suffisant pour ce cas d'usage.

---

## 3. Personnaliser les entreprises

Dans `main.py`, modifier le dictionnaire `COMPANIES` :

```python
COMPANIES = {
    "Nom affiché": "Nom complet pour la recherche",
    "LVMH":        "LVMH Moët Hennessy",
    "Apple":       "Apple Inc",
    "TotalEnergies": "TotalEnergies SE",
}
```

---

## 4. Déployer sur GCP

```bash
# 1. Remplir les variables en haut de deploy.sh
nano deploy.sh

# 2. Se connecter à GCP
gcloud auth login

# 3. Lancer le déploiement (environ 3-5 minutes)
chmod +x deploy.sh && ./deploy.sh
```

---

## 5. Tester immédiatement

```bash
# Forcer l'envoi du rapport hebdo maintenant
gcloud scheduler jobs run weekly-report-job --location=europe-west1

# Forcer une vérification d'alertes maintenant
gcloud scheduler jobs run alert-monitor-job --location=europe-west1
```

Tu peux aussi voir les logs :
```bash
gcloud functions logs read weekly_report --region=europe-west1 --limit=20
gcloud functions logs read alert_monitor --region=europe-west1 --limit=20
```

---

## Ajuster le seuil d'alerte

Dans `main.py`, modifier `ALERT_THRESHOLD` (valeur entre 1 et 10) :

```python
ALERT_THRESHOLD = 8   # 8 = très sélectif (recommandé)
                      # 6 = plus d'alertes, moins critiques
                      # 9 = seulement les catastrophes majeures
```

---

## Structure des fichiers

```
stock_news_agent/
├── main.py           ← Toute la logique (à modifier pour vos entreprises)
├── requirements.txt  ← Dépendances Python
├── deploy.sh         ← Script de déploiement GCP (remplir les variables)
└── README.md         ← Ce fichier
```
