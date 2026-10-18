"""Build the same wine cards the app builds (backend/product.py), straight from the repo files,
so offline checks run on exactly what the recommender sees in production."""
import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def plain(text):
    return re.sub('<[^>]*>', '', str(text or '')).strip()


def load_cards(repo=REPO):
    with open(repo / 'seed/catalog.csv', encoding='utf-8-sig') as f:
        rows = {r['Slug'].strip(): r for r in csv.DictReader(f) if r.get('Slug', '').strip()}
    portal = repo / 'backend/content/portal_wines.json'
    extra = json.loads(portal.read_text())['wines'] if portal.exists() else {}
    cards = {}
    for slug, row in rows.items():
        ex = extra.get(slug, {})
        cards[slug] = {
            'slug': slug, 'name': plain(row.get('Название вина')), 'winery': plain(row.get('Винодельня')),
            'category': plain((ex.get('category') or {}).get('name') or row.get('Категория')),
            'region': plain(row.get('Регион')), 'grapes': plain(row.get('Сорт винограда')),
            'description': plain(ex.get('description') or row.get('Описание')),
            'alcohol': ex.get('alcohol'),
            'temperature': (plain(ex.get('temperature')) + ' °C' if ex.get('temperature') else ''),
            'pairings': [plain(d.get('name')) for d in ex.get('dishes', []) if d.get('name')],
        }
    return cards
