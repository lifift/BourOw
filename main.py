"""
Agent IA — Suivi news boursières
Version 3 : SDK google-genai, double modèle, user-agent navigateur, NewsAPI + RSS fallback
"""

import os
import json
import smtplib
import feedparser
import requests
import functions_framework
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — à adapter
# ──────────────────────────────────────────────────────────────────────────────

# Format : "Nom affiché dans le rapport": ("Nom court de recherche", "Requête longue avec mots-clés secteur")
# Le nom court est utilisé en priorité ; la requête longue sert de fallback si aucun résultat.
COMPANIES = {
    "TotalEnergies":      ("TotalEnergies",           "TotalEnergies SE pétrole énergie"),
    "Agnico Eagle":       ("Agnico Eagle Mines",      "Agnico Eagle Mines gold mining"),
    "Cameco":             ("Cameco",                  "Cameco uranium mining"),
    "Newmont":            ("Newmont",                 "Newmont Corporation gold mining"),
    "Wheaton Precious":   ("Wheaton Precious Metals", "Wheaton Precious Metals silver streaming"),
    "Air Liquide":        ("Air Liquide",             "Air Liquide industrial gases"),
    "AMG Critical Mat.":  ("AMG Critical Materials",  "AMG Critical Materials metals"),
    "Aurubis":            ("Aurubis",                 "Aurubis copper smelting"),
    "Derichebourg":       ("Derichebourg",            "Derichebourg recyclage ferraille"),
    "FDJ":                ("FDJ",                     "Française des Jeux FDJ loterie"),
    "Fugro":              ("Fugro",                   "Fugro geotechnical survey offshore"),
    "Legrand":            ("Legrand",                 "Legrand électrique bâtiment"),
    "Manitou":            ("Manitou BF",              "Manitou BF manutention chariots"),
    "Neurones":           ("Neurones IT",             "Neurones IT services informatiques"),
    "Schneider Electric": ("Schneider Electric",      "Schneider Electric énergie automatisation"),
}

# Variables d'environnement — définies dans deploy.sh, jamais en dur dans le code
GMAIL_USER     = os.environ.get("GMAIL_USER")       # ex: tonmail@gmail.com
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")   # Mot de passe d'application Gmail (16 car.)
RECIPIENT      = os.environ.get("RECIPIENT_EMAIL")  # Destinataire (peut être identique à GMAIL_USER)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")   # Depuis https://aistudio.google.com/app/apikey
NEWSAPI_KEY    = os.environ.get("NEWSAPI_KEY", "")  # Depuis https://newsapi.org/register (gratuit)

# Seuil d'alerte : Gemini doit attribuer un score >= cette valeur pour envoyer un email
ALERT_THRESHOLD = 8  # sur 10

# Gemini 3.1 Pro pour le rapport hebdo (qualité maximale, ~20 appels/semaine << 250 RPD)
MODEL_WEEKLY = "gemini-3.1-pro-preview"
# Gemini 3.1 Flash-Lite pour les alertes (rapide, 150 000 RPD, toutes les 4h)
MODEL_ALERT  = "gemini-3.1-flash-lite-preview"

# User-agent navigateur pour contourner le blocage des IPs GCP par Google News
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RSS_HEADERS = {
    "User-Agent":      BROWSER_UA,
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
}

# Client Gemini (nouveau SDK google-genai, l'ancien google-generativeai est déprécié)
client = genai.Client(api_key=GEMINI_API_KEY)


# ──────────────────────────────────────────────────────────────────────────────
# COLLECTE DES NEWS
# ──────────────────────────────────────────────────────────────────────────────

def fetch_via_newsapi(query: str, hours_back: int) -> list[dict]:
    """
    Source principale : NewsAPI.org (gratuit, 100 req/jour).
    Retourne une liste d'articles ou [] si la clé est absente / en cas d'erreur.
    """
    if not NEWSAPI_KEY:
        return []

    from_date = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        query,
                "from":     from_date,
                "sortBy":   "publishedAt",
                "pageSize": 10,
                "apiKey":   NEWSAPI_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        return [
            {
                "title":   a.get("title", ""),
                "summary": (a.get("description") or a.get("content") or "")[:300],
                "link":    a.get("url", ""),
                "date":    (a.get("publishedAt") or "")[:10],
            }
            for a in r.json().get("articles", [])
            if a.get("title")  # ignorer les articles sans titre
        ]
    except Exception as e:
        print(f"[NewsAPI] Erreur pour '{query}': {e}")
        return []


def fetch_via_rss(query: str, hours_back: int) -> list[dict]:
    """
    Fallback : Google News RSS.
    Utilise requests + user-agent navigateur pour éviter le blocage des IPs GCP.
    """
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=fr&gl=FR&ceid=FR:fr"
    try:
        response = requests.get(url, headers=RSS_HEADERS, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"[RSS] Erreur fetch pour '{query}': {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    articles = []

    for entry in feed.entries[:15]:
        # Parsing de date — fallback à maintenant si le champ est absent ou malformé
        published = datetime.now(timezone.utc)
        if getattr(entry, "published_parsed", None):
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass

        if published < cutoff:
            continue

        # Google News glisse le nom du média en fin de titre ("Titre — Le Monde") — on le retire
        title = entry.get("title", "")
        if " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()

        articles.append({
            "title":   title,
            "summary": entry.get("summary", "")[:400],
            "link":    entry.get("link", ""),
            "date":    published.strftime("%d/%m/%Y"),
        })

    return articles


def fetch_news(short_name: str, long_query: str, hours_back: int) -> list[dict]:
    """
    Stratégie 3 couches :
    1. NewsAPI (fr) — fiable depuis GCP, 100 req/jour
    2. RSS Google News avec le nom court
    3. RSS Google News avec la requête longue enrichie en mots-clés
    S'arrête dès qu'une source retourne au moins un article.
    """
    for source, query in [
        ("NewsAPI",   short_name),
        ("RSS-court", short_name),
        ("RSS-long",  long_query),
    ]:
        if source == "NewsAPI":
            articles = fetch_via_newsapi(query, hours_back)
        else:
            articles = fetch_via_rss(query, hours_back)

        if articles:
            print(f"  [{source}] {short_name}: {len(articles)} articles")
            return articles

    print(f"  [WARN] {short_name}: 0 articles (toutes sources épuisées)")
    return []


# ──────────────────────────────────────────────────────────────────────────────
# ANALYSE PAR GEMINI
# ──────────────────────────────────────────────────────────────────────────────

def analyze_for_weekly(display_name: str, articles: list[dict]) -> str:
    """
    Utilise gemini-3.1-pro-preview (le plus puissant).
    Retourne 1 à 3 bullet points résumant la semaine pour cette entreprise.
    """
    if not articles:
        return "• Aucune news trouvée — vérifier le nom de recherche dans COMPANIES."

    news_text = "\n".join(
        f"- {a['date']} : {a['title']}. {a['summary'][:200]}"
        for a in articles[:10]
    )
    prompt = f"""Tu es un assistant financier concis. Voici les news de la semaine pour {display_name} :

{news_text}

Rédige 1 à 3 bullet points (•) en français, ultra-concis, pour un investisseur particulier.
Ne mentionne que ce qui a un impact réel sur le cours du titre.
Si rien de notable : une seule ligne "• Semaine calme, aucun événement majeur."
"""
    return client.models.generate_content(model=MODEL_WEEKLY, contents=prompt).text.strip()


def analyze_for_alert(display_name: str, articles: list[dict]) -> dict | None:
    """
    Utilise gemini-3.1-flash-lite-preview (rapide, quota quasi illimité).
    Retourne un dict {score, titre, raison} si score >= ALERT_THRESHOLD, sinon None.
    Exemples d'alertes : marée noire, fraude majeure, profit warning sévère,
    sanction réglementaire massive, acquisition transformante, guerre impactant l'activité.
    """
    if not articles:
        return None

    news_text = "\n".join(
        f"- {a['title']}. {a['summary'][:300]}"
        for a in articles[:5]
    )
    prompt = f"""Tu analyses des news financières pour détecter des événements CRITIQUES concernant {display_name}.

News récentes :
{news_text}

Un événement est CRITIQUE (score >= 8/10) s'il peut faire bouger le cours de +/- 5% ou plus.
Exemples : catastrophe environnementale causée par l'entreprise, fraude/scandale majeur,
faillite, acquisition transformante, sanction réglementaire massive, profit warning sévère,
guerre impactant directement l'activité principale.

Réponds UNIQUEMENT en JSON brut, sans balises markdown :
{{"score": <entier 1-10>, "titre": "<titre du principal article>", "raison": "<explication en 1 phrase>"}}
"""
    try:
        text = client.models.generate_content(model=MODEL_ALERT, contents=prompt).text
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)
        return data if data.get("score", 0) >= ALERT_THRESHOLD else None
    except Exception as e:
        print(f"[WARN] JSON Gemini invalide pour {display_name}: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# CONSTRUCTION DES EMAILS
# ──────────────────────────────────────────────────────────────────────────────

def build_weekly_html(summaries: dict[str, str]) -> str:
    week = datetime.now().strftime("Semaine du %d/%m/%Y")
    rows = ""
    for company, bullets in summaries.items():
        # Fond légèrement grisé si aucune news ou semaine calme
        empty = "Aucune news" in bullets or "calme" in bullets.lower()
        bg = "#fafafa" if empty else "#ffffff"
        rows += f"""
        <tr style="background:{bg}">
          <td style="padding:12px 16px;font-weight:600;white-space:nowrap;
                     border-bottom:1px solid #eee;vertical-align:top">{company}</td>
          <td style="padding:12px 16px;border-bottom:1px solid #eee;
                     line-height:1.7;color:#333">{bullets.replace(chr(10), "<br>")}</td>
        </tr>"""

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:720px;margin:auto;color:#222">
<h2 style="background:#1a1a2e;color:#fff;padding:18px 24px;border-radius:8px 8px 0 0;margin:0">
  📈 Rapport boursier — {week}
</h2>
<table style="width:100%;border-collapse:collapse;border:1px solid #ddd;border-top:none">
  <thead>
    <tr style="background:#f5f5f5">
      <th style="padding:10px 16px;text-align:left;width:180px">Entreprise</th>
      <th style="padding:10px 16px;text-align:left">Résumé de la semaine</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p style="color:#999;font-size:12px;margin-top:16px">
  Analyse : Gemini 3.1 Pro · Sources : NewsAPI + Google News RSS
</p>
</body></html>"""


def build_alert_html(company: str, alert: dict, articles: list[dict]) -> str:
    links = "".join(
        f'<li><a href="{a["link"]}">{a["title"]}</a></li>'
        for a in articles[:3]
    )
    return f"""<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222">
<div style="background:#c0392b;color:#fff;padding:18px 24px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">🚨 ALERTE — {company}</h2>
  <p style="margin:6px 0 0;opacity:.9">Score de criticité : {alert["score"]}/10</p>
</div>
<div style="border:1px solid #e0c0c0;border-top:none;padding:20px 24px;
            background:#fff9f9;border-radius:0 0 8px 8px">
  <p><strong>Article détecté :</strong> {alert["titre"]}</p>
  <p style="background:#fdf0f0;border-left:4px solid #c0392b;
            padding:12px 16px;border-radius:4px;margin:16px 0">{alert["raison"]}</p>
  <p><strong>Sources :</strong></p>
  <ul style="line-height:2">{links}</ul>
</div>
<p style="color:#999;font-size:12px;margin-top:12px">
  Alerte automatique · {datetime.now().strftime("%d/%m/%Y à %H:%M")}
</p>
</body></html>"""


def send_email(subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
    print(f"[OK] Email envoyé : {subject}")


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINTS GCP CLOUD FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

@functions_framework.http
def weekly_report(request):
    """
    Déclenché chaque lundi à 8h par Cloud Scheduler.
    Collecte les news de la semaine, les résume avec Gemini 3.1 Pro,
    et envoie le rapport par email.
    """
    print("[START] weekly_report")
    summaries = {}
    for display_name, (short_name, long_query) in COMPANIES.items():
        articles = fetch_news(short_name, long_query, hours_back=168)  # 7 jours
        summaries[display_name] = analyze_for_weekly(display_name, articles)

    send_email(
        subject=f"📈 Rapport boursier du {datetime.now().strftime('%d/%m/%Y')}",
        html_body=build_weekly_html(summaries),
    )
    return f"Rapport envoyé ({len(summaries)} entreprises)", 200


@functions_framework.http
def alert_monitor(request):
    """
    Déclenché toutes les 4h par Cloud Scheduler.
    Scanne les news récentes, les évalue avec Gemini 3.1 Flash-Lite,
    et envoie un email immédiat si le score d'alerte >= ALERT_THRESHOLD.
    """
    print("[START] alert_monitor")
    alerts_sent = 0
    for display_name, (short_name, long_query) in COMPANIES.items():
        articles = fetch_news(short_name, long_query, hours_back=4)
        if not articles:
            continue
        alert = analyze_for_alert(display_name, articles)
        if alert:
            send_email(
                subject=f"🚨 ALERTE {display_name} — Score {alert['score']}/10",
                html_body=build_alert_html(display_name, alert, articles),
            )
            alerts_sent += 1
            print(f"  ALERTE envoyée : {display_name} (score {alert['score']})")

    return f"{alerts_sent} alerte(s) envoyée(s)", 200
