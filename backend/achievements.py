"""Profile-scoped exploration rewards. Scans measure discovery, not consumption."""
import json
import re
import time
from product import normalized

ACHIEVEMENTS = (
    ('first-label', 'Первое знакомство', 'Первооткрыватель', 'wines', 1, 'first', 'Распознайте первое вино.'),
    ('five-labels', 'Своя пятёрка', 'Любознательный', 'wines', 5, 'five', 'Распознайте 5 разных вин.'),
    ('fifteen-labels', 'Кругозор', 'Исследователь', 'wines', 15, 'cellar', 'Распознайте 15 разных вин.'),
    ('thirty-labels', 'Атлас открытий', 'Хранитель открытий', 'wines', 30, 'atlas', 'Распознайте 30 разных вин.'),
    ('three-grapes', 'Азбука сортов', 'Знаток сортов', 'grapes', 3, 'grapes', 'Распознайте вина трёх сортов. Учитываются моносортовые вина.'),
    ('three-wineries', 'Почерк винодела', 'Исследователь виноделен', 'wineries', 3, 'winery', 'Распознайте вина трёх разных виноделен.'),
    ('three-regions', 'Винная география', 'Путешественник', 'regions', 3, 'journey', 'Распознайте вина из трёх регионов каталога.'),
    ('three-colors', 'Палитра вина', 'Коллекционер оттенков', 'colors', 3, 'palette', 'Распознайте вина трёх цветов: белое, красное, розовое или оранжевое.'),
    ('collection-first-save', 'Ваш вкус вдохновляет', 'Вдохновитель', 'collection_saves', 1, 'five', 'Вашу коллекцию сохранил себе 1 человек.'),
    ('collection-ten-saves', 'Выбор сообщества', 'Куратор', 'collection_saves', 10, 'cellar', 'Вашу коллекцию сохранили себе 10 человек.'),
    ('collection-fifty-saves', 'Коллекция с именем', 'Голос сообщества', 'collection_saves', 50, 'atlas', 'Вашу коллекцию сохранили себе 50 человек.'),
)


def collection_reach(db, profile):
    if profile.startswith('system:'): return 0
    row=db.execute('''SELECT count(DISTINCT s.profile) AS n FROM collection_lists l
        JOIN collection_saves s ON s.source_id=l.id JOIN wine_accounts a ON a.profile=s.profile
        WHERE l.profile=? AND s.profile!=l.profile AND s.profile NOT LIKE 'system:%'
        GROUP BY l.id ORDER BY n DESC LIMIT 1''',(profile,)).fetchone()
    return row['n'] if row else 0


def award_collection_badges(db, profile):
    reach=collection_reach(db,profile)
    for ident,title,status,metric,target,art,description in ACHIEVEMENTS:
        if metric=='collection_saves' and reach>=target:
            db.execute('INSERT OR IGNORE INTO collection_badges VALUES(?,?,?,?)',(profile,ident,status,time.time()))
    return reach


def scanned_wines(store, profile, cards):
    """Only completed recognitions owned by this profile; repeated slugs count once."""
    with store.db() as db:
        rows = db.execute('''SELECT s.result FROM wine_profile_scans p JOIN scans s ON s.id=p.scan_id
            WHERE p.profile=? AND s.status='done' ORDER BY p.created''', (profile,)).fetchall()
    found = set()
    for row in rows:
        try:
            result = json.loads(row['result'])
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict):
            continue
        version = str(result.get('model_version', '')).lower()
        slug = result.get('slug')
        if (not version or any(x in version for x in ('fake', 'test', 'design', 'preview'))
                or result.get('no_wine') or not isinstance(slug, str) or slug not in cards):
            continue
        found.add(slug)
    return found


def achievement_snapshot(store, profile, cards):
    found = scanned_wines(store, profile, cards)
    wines = [cards[s] for s in found]
    grapes = set()
    for wine in wines:
        parts = [normalized(x) for x in re.split(r'[,;/+]', wine.get('grapes', '')) if normalized(x)]
        if len(parts) == 1 and not any(word in parts[0] for word in ('купаж', 'смесь', 'не указан')):
            grapes.add(parts[0])
    colors = {color for w in wines for color in ('белое', 'красное', 'розовое', 'оранжевое')
              if color in normalized(w.get('category', ''))}
    counts = {'wines': len(found), 'grapes': len(grapes), 'colors': len(colors),
              'wineries': len({normalized(w['winery']) for w in wines if w.get('winery')}),
              'regions': len({normalized(w['region']) for w in wines if w.get('region')})}
    items = []
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        counts['collection_saves']=award_collection_badges(db,profile)
        for ident, title, status, metric, target, art, description in ACHIEVEMENTS:
            progress = min(counts[metric], target)
            if progress == target:
                db.execute('INSERT OR IGNORE INTO collection_badges VALUES(?,?,?,?)',
                           (profile, ident, status, time.time()))
            earned = db.execute('SELECT earned FROM collection_badges WHERE profile=? AND challenge_id=?',
                                (profile, ident)).fetchone()
            if metric=='collection_saves' and not earned:
                description=f'Получите {target} '+('сохранение' if target==1 else 'сохранений')+' одной коллекции от разных участников.'
            items.append(dict(id=ident, title=title, status=status, metric=metric, target=target,
                              progress=progress, earned=bool(earned), earned_at=earned['earned'] if earned else None,
                              image='/assets/achievements/'+art+'.webp', description=description))
    status = next((x['status'] for x in reversed(items) if x['metric']=='collection_saves' and x['earned']), '') or next((x['status'] for x in reversed(items[:4]) if x['earned']), 'Всё впереди')
    return {'items': items, 'earned_count': sum(x['earned'] for x in items), 'status': status,
            'unique_wines': len(found), 'metrics': counts}
