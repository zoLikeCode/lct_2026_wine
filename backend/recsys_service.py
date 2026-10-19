"""Recommendation modes built on the user's own collection, the taste passport, newcomer
onboarding, and feedback/impression logs. Ranking primitives live in recsys.py; the home feed
and query ranking in Product.recommendations. Frontend contract: docs/recsys_api.md."""
import collections
import json
import re
import time
import uuid

from fastapi import HTTPException

from recsys import grapes_of, sweetness_level
from taste import AROMAS, AXES, AXIS_NAMES, OCCASIONS, QUIZ, QUIZ_OPTIONS, TILES, quiz_label, quiz_profile

MODES = ('for_you', 'new', 'cellar', 'refill')
HIDE_DAYS = 30
COLORS = {'красное', 'белое', 'розовое', 'оранжевое'}
SWEETS = {'сухое', 'полусухое', 'полусладкое', 'сладкое'}
VERDICTS = ('like', 'nope', 'skip')
# Passport tabs: which wines each one covers (colour of a still wine, or any sparkling).
KINDS = {'red': ('Красное', 'красное'), 'white': ('Белое', 'белое'), 'rose': ('Розовое', 'розовое'),
         'sparkling': ('Игристое', None)}
# Wine page scales, Vivino-style: (axis, left end, right end). Tannin only for still reds.
SCALES = (('body', 'Лёгкое', 'Насыщенное'), ('tannin', 'Мягкое', 'Терпкое'),
          ('sweetness', 'Сухое', 'Сладкое'), ('acidity', 'Округлое', 'Освежающее'))
# How a wine differs from the user's taste on one axis: (more, less).
AXIS_WORDS = {'body': ('насыщеннее', 'легче'), 'tannin': ('терпче', 'мягче'), 'sweetness': ('слаще', 'суше'),
              'acidity': ('кислотнее', 'мягче по кислотности')}
AXIS_GENITIVE = {'body': 'телу', 'tannin': 'танинам', 'sweetness': 'сладости', 'acidity': 'кислотности'}
# Occasion words in a list's name/goal → what suits it (sparkling, sweetness, dishes, taste occasions).
OCCASION_RULES = [
    (('нов', 'праздн', 'рожд', 'свадьб', 'юбил', 'торжеств', 'корпорат', 'вечеринк'), {'sparkling': 1.0, 'occasion': 'celebration'}),
    (('шашлык', 'мяс', 'стейк', 'барбекю', 'гриль', 'bbq'), {'dish': ('мяс', 'bbq', 'гриль'), 'occasion': 'dinner'}),
    (('рыб', 'морепр', 'устриц', 'суши'), {'dish': ('рыб', 'морепродукт', 'устриц'), 'occasion': 'dinner'}),
    (('сыр',), {'dish': ('сыр',)}),
    (('десерт', 'сладк', 'торт'), {'dish': ('десерт',), 'occasion': 'dessert'}),
    (('пикник', 'лет', 'дач', 'пляж'), {'occasion': 'picnic'}),
    (('ужин', 'гост', 'семь'), {'occasion': 'dinner'}),
    (('подар',), {'popular': 1.0, 'occasion': 'special'}),
    (('свидан', 'романт'), {'occasion': 'special', 'sparkling': 0.5}),
]


def _norm(text):
    return re.sub(r'[^а-яa-z0-9]+', ' ', str(text or '').lower().replace('ё', 'е')).strip()


class RecsysService:
    def __init__(self, product):
        self.p = product
        self.store = product.store
        self.rec = product.recsys
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS recsys_events(id TEXT PRIMARY KEY,profile TEXT,slug TEXT,mode TEXT,
                action TEXT,position INTEGER,request_id TEXT,created REAL);
            CREATE INDEX IF NOT EXISTS recsys_events_profile ON recsys_events(profile,action,created);
            CREATE TABLE IF NOT EXISTS recsys_onboarding(profile TEXT PRIMARY KEY,answers TEXT,updated REAL);
            CREATE TABLE IF NOT EXISTS recsys_swipes(profile TEXT,slug TEXT,verdict TEXT,favorited INTEGER DEFAULT 0,
                created REAL,PRIMARY KEY(profile,slug));
            ''')
        self.dishes = sorted({d for w in product.cards.values() for d in w['pairings']})

    # ---- state used by Product.recommendations -------------------------------------------
    def not_for_me(self, profile):
        with self.store.db() as db:
            return {r['slug'] for r in db.execute(
                "SELECT slug FROM recsys_events WHERE profile=? AND action='not_for_me'", (profile,))}

    def hidden(self, profile):
        since = time.time() - HIDE_DAYS * 86400
        with self.store.db() as db:
            return {r['slug'] for r in db.execute(
                "SELECT slug FROM recsys_events WHERE profile=? AND action IN ('dismiss','not_for_me') AND created>=?",
                (profile, since))}

    def swipes(self, profile):
        with self.store.db() as db:
            return {r['slug']: r['verdict'] for r in db.execute(
                'SELECT slug,verdict FROM recsys_swipes WHERE profile=?', (profile,))}

    def answers(self, profile):
        with self.store.db() as db:
            r = db.execute('SELECT answers FROM recsys_onboarding WHERE profile=?', (profile,)).fetchone()
        try:
            return json.loads(r['answers']) if r else None
        except ValueError:
            return None

    @staticmethod
    def effective(a):
        """Answers plus what the tiles imply: «Сладкое, десертное» alone means sweet; tiles without it
        mean dry; no «Игристое» among the tiles means still wine."""
        a = dict(a or {})
        tiles = a.get('tiles') or []
        if tiles and not a.get('sweetness'):
            a['sweetness'] = (['полусладкое', 'сладкое'] if tiles == ['dessert'] else
                              ['сухое', 'полусухое'] + (['полусладкое', 'сладкое'] if 'dessert' in tiles else []))
        if tiles and 'sparkling' not in a:
            a['sparkling'] = True if tiles == ['bubbles'] else (None if 'bubbles' in tiles else False)
        return a

    def onboarding_seeds(self, profile):
        """Onboarding answers as virtual liked wines: one per chosen tile (or one for the style
        alone). Weight 0.6 each, so three real ratings already outweigh them."""
        a = self.effective(self.answers(profile))
        if not a:
            return [], {}
        colors, dishes = a.get('colors', []), a.get('dishes', [])
        sweets = a.get('sweetness', [])
        seeds, labels = [], {}
        quiz = a.get('quiz') or {}
        if quiz:  # six answers are one virtual wine; all six weigh a little less than one rating of 5
            q = quiz_profile(quiz)
            vec = self.rec.virtual_vector(colors, sweets, q['sparkling'], q['taste'], q['aromas'], dishes)
            seeds.append(('onboarding:quiz', vec, 0.4 + 0.08 * len(quiz)))
            labels['onboarding:quiz'] = quiz_label(quiz)
        for tile in a.get('tiles', []) or ([] if quiz else [None]):
            spec = TILES.get(tile, {}) if tile else {}
            if not (spec or colors or sweets or dishes or a.get('sparkling')):
                continue
            label = 'onboarding:' + (tile or 'style')
            taste = dict(spec.get('taste', {}))
            tile_sweets = ['полусладкое', 'сладкое'] if tile == 'dessert' else sweets
            vec = self.rec.virtual_vector(colors, tile_sweets, bool(spec.get('sparkling') or a.get('sparkling')),
                                          taste, spec.get('aromas', ()), dishes)
            seeds.append((label, vec, 0.6 if tile else 0.4))
            labels[label] = spec.get('label') or ', '.join(colors + sweets) or 'ваши ответы'
        return seeds, labels

    def has_taste(self, profile):
        """Whether anything personal is known yet: own ratings, bottles, swipes or onboarding answers."""
        pos, neg = self.rec.profile(self.p.taste_signals(profile))
        return bool(pos or neg or self.onboarding_seeds(profile)[0])

    @classmethod
    def fits_answers(cls, answers, style):
        answers = cls.effective(answers)
        color, sweet, sparkling = style
        if answers.get('colors') and color and color not in answers['colors']:
            return False
        if answers.get('sparkling') is False and sparkling:
            return False
        if answers.get('sparkling') is True and not sparkling and not (answers.get('tiles') or [])[1:]:
            return False
        level = {'брют': 'сухое', 'экстра брют': 'сухое'}.get(sweet, sweet)
        if answers.get('sweetness') and level not in answers['sweetness']:
            return False  # unknown sweetness («Белое») does not pass a stated preference either
        return True

    # ---- endpoints ---------------------------------------------------------------------
    def endpoint(self, profile, mode='for_you', q='', limit=8, log=True):
        if mode not in MODES:
            raise HTTPException(422, 'mode: ' + ', '.join(MODES))
        if type(limit) is not int or not 1 <= limit <= 24:
            raise HTTPException(422, 'limit от 1 до 24')
        q = str(q or '')[:160]
        if mode == 'for_you':
            items, empty = self.p.recommendations(profile, q, limit), None
        elif mode == 'new':
            items, empty = self.explore(profile, limit)
        elif mode == 'cellar':
            items, empty = self.cellar(profile, q, limit)
        else:
            items, empty = self.refill(profile, limit)
        request_id = self.log_impressions(profile, items, mode) if log else None
        return {'mode': mode, 'items': items, 'request_id': request_id, 'empty': None if items else empty}

    def log_impressions(self, profile, items, mode):
        request_id = uuid.uuid4().hex
        now = time.time()
        with self.store.db() as db:
            db.executemany('INSERT INTO recsys_events VALUES(?,?,?,?,?,?,?,?)', [
                (uuid.uuid4().hex, profile, w['slug'], mode, 'impression', k, request_id, now)
                for k, w in enumerate(items)])
        return request_id

    def feedback(self, profile, body):
        if not isinstance(body, dict):
            raise HTTPException(422, 'Ожидается объект')
        slug, action = body.get('slug'), body.get('action')
        self.p.require_slug(slug)
        if action not in ('click', 'dismiss', 'not_for_me', 'undo'):
            raise HTTPException(422, 'action: click, dismiss, not_for_me или undo')
        mode = body.get('mode') if body.get('mode') in MODES + ('home', 'list') else None
        request_id = body.get('request_id') if isinstance(body.get('request_id'), str) and len(body['request_id']) <= 64 else None
        with self.store.db() as db:
            if action == 'undo':
                db.execute("DELETE FROM recsys_events WHERE profile=? AND slug=? AND action IN ('dismiss','not_for_me')",
                           (profile, slug))
            else:
                db.execute('INSERT INTO recsys_events VALUES(?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, profile, slug, mode, action, None, request_id, time.time()))
        return {'ok': True}

    def save_onboarding(self, profile, body):
        if not isinstance(body, dict):
            raise HTTPException(422, 'Ожидается объект')

        def pick(key, allowed, cap):
            vals = body.get(key, [])
            if not isinstance(vals, list) or any(v not in allowed for v in vals) or len(vals) > cap:
                raise HTTPException(422, f'{key}: значения из списка, не больше {cap}')
            return list(dict.fromkeys(vals))
        answers = {'colors': pick('colors', COLORS, 4), 'sweetness': pick('sweetness', SWEETS, 4),
                   'dishes': pick('dishes', set(self.dishes), 6), 'tiles': pick('tiles', set(TILES), 3)}
        if 'sparkling' in body:
            if type(body['sparkling']) is not bool:
                raise HTTPException(422, 'sparkling: true/false')
            answers['sparkling'] = body['sparkling']
        with self.store.db() as db:
            db.execute('INSERT INTO recsys_onboarding VALUES(?,?,?) ON CONFLICT(profile) DO UPDATE SET '
                       'answers=excluded.answers,updated=excluded.updated',
                       (profile, json.dumps(answers, ensure_ascii=False), time.time()))
        return {'ok': True, 'answers': answers,
                'recommendations': self.endpoint(profile, 'for_you', '', 8)}

    def onboarding_options(self, profile):
        return {'answers': self.answers(profile), 'colors': sorted(COLORS), 'sweetness': sorted(SWEETS),
                'dishes': self.dishes, 'tiles': [{'id': k, 'label': v['label']} for k, v in TILES.items()]}

    # ---- modes -------------------------------------------------------------------------
    def _context(self, profile):
        signals = self.p.taste_signals(profile)
        virtual, labels = self.onboarding_seeds(profile)
        personal, source, confidence, penalty = self.rec.personal(signals, virtual=virtual)
        prior = self.rec.prior(*self.p.aggregates())
        return signals, virtual, labels, personal, source, confidence, penalty, prior

    def _cards(self, chosen, profile, extra=None):
        cards = self.p.cards_for([x[1] for x in chosen], profile)
        return [{**w, 'reason': x[2], **((extra or {}).get(w['slug'], {}))} for w, x in zip(cards, chosen)]

    def explore(self, profile, limit):
        """Something new: a grape or region the collection has not got yet, close to the user's taste."""
        signals, virtual, labels, personal, source, conf, penalty, prior = self._context(profile)
        mine = [s for s, v in signals.items() if self.rec.evidence(v) > 0]
        if not mine:  # nothing real to be 'new' relative to: show the unusual side, within the answers
            return self._unusual(profile, limit), None
        known_grapes = {g for s in mine for g in grapes_of(self.p.cards[s])}
        levels = {sweetness_level(self.rec.styles[self.rec.index[s]]) for s in mine}
        known_regions = {self.p.cards[s]['region'] for s in mine}
        hidden = self.hidden(profile) | set(signals)
        scored = []
        for slug, i in self.rec.index.items():
            if slug in hidden:
                continue
            w = self.p.cards[slug]
            if levels and sweetness_level(self.rec.styles[i]) not in levels:
                continue  # new grape or region, yes; a sweet wine for a dry-wine drinker, no
            grapes = set(grapes_of(w))
            new_grape = bool(grapes) and not grapes & known_grapes
            new_region = w['region'] and w['region'] not in known_regions
            if not (new_grape or new_region):
                continue
            score = personal[i] * (1.15 if new_grape and new_region else 1.0) - 0.6 * penalty[i] + 0.1 * prior[i]
            what = ('новый для вас сорт ' + grapes_of(w)[0].title() if new_grape else
                    'новый для вас регион — ' + w['region'])
            src = source[i]
            near = (' · по вкусу близко к «' + self.p.cards[self.rec.slugs[src]]['name'] + '»'
                    if isinstance(src, int) and self.rec.sim(i, src) >= 0.35 else '')
            scored.append((score, slug, 'Попробуйте новое: ' + what + near))
        chosen = self.rec.diversify(scored, limit)
        return self._cards(chosen, profile), 'Оцените пару вин — и подберём новое рядом с вашим вкусом'

    def _unusual(self, profile, limit):
        """«Попробовать новое» for someone with no history: the less common sides of the catalogue
        (orange, red sparkling, rare local grapes), so it is not a copy of «Для вас»."""
        _, _, _, _, _, _, _, prior = self._context(profile)
        answers = self.answers(profile)
        shares = self.rec.style_shares()
        grape_count = collections.Counter(self.rec.main_grape)
        scored = []
        for slug, i in self.rec.index.items():
            if answers and not self.fits_answers(answers, self.rec.styles[i]):
                continue
            rare_style = 1 - shares.get(self.rec.interest(i), 0) * 4
            g = self.rec.main_grape[i]
            rare_grape = 1.0 if g and grape_count[g] <= 8 else 0.0
            score = 0.5 * rare_style + rare_grape + prior[i]
            what = ('редкий сорт ' + g.title() if rare_grape else (self.p.cards[slug]['category'] or 'вино').lower())
            scored.append((score, slug, 'Необычное: ' + what + ', ' + (self.p.cards[slug]['region'] or 'Россия')))
        # mostly dry, some sweet: rare styles are mostly sweet, and that is not the whole picture
        interests = {slug: sweetness_level(self.rec.styles[self.rec.index[slug]]) for _, slug, _ in scored}
        shares = {'dry': 0.55, 'medium': 0.15, 'sweet': 0.2, 'unknown': 0.1}
        return self._cards(self.rec.diversify(scored, limit, interests=interests, shares=shares), profile)

    def cellar(self, profile, q, limit):
        """What to open tonight, from the bottles the user has; optionally for a dish."""
        signals, virtual, labels, personal, source, conf, penalty, prior = self._context(profile)
        owned = {s: v for s, v in signals.items() if v.get('quantity')}
        if not owned:
            return [], 'Добавьте бутылки в коллекцию — тогда подскажем, что открыть'
        words = _norm(q)
        wanted = [targets for keys, rule in OCCASION_RULES for targets in [rule.get('dish')]
                  if targets and any(k in words for k in keys)]
        scored = []
        for slug, sig in owned.items():
            w, i = self.p.cards[slug], self.rec.index[slug]
            dishes = _norm(' '.join(w['pairings']))
            match = [t for targets in wanted for t in targets if t in dishes]
            fits = self.style_fits_dish(self.rec.styles[i], wanted)
            score = (10 if match else 6 if fits else 0) + (sig.get('rating') or 3) + 0.5 * min(sig['quantity'], 4) - (
                0.5 * sig.get('opened', 0))
            reason = f'У вас {sig["quantity"]} бут.'
            if match:
                reason += ' · к блюдам: ' + ', '.join(w['pairings'][:3]).lower()
            elif wanted and fits:
                reason += ' · ' + fits
            elif wanted:
                continue  # a red with fish is not an answer, even from your own shelf
            if sig.get('rating'):
                reason += f' · ваша оценка {sig["rating"]}'
            scored.append((score, slug, reason))
        if not scored:
            return [], 'Из ваших бутылок к этому ничего не подходит — посмотрите подбор из каталога'
        scored.sort(key=lambda x: -x[0])
        extra = {s: {'owned': True, 'quantity': v['quantity']} for s, v in owned.items()}
        return self._cards(scored[:limit], profile, extra), None

    @staticmethod
    def style_fits_dish(style, wanted):
        """Classic style rules when the portal has no tag for this wine and dish."""
        color, sweet, sparkling = style
        flat = {t for targets in wanted for t in targets}
        level = sweetness_level(style)
        if flat & {'рыб', 'морепродукт', 'устриц'} and (color in ('белое', 'розовое') or sparkling) and level != 'sweet':
            return 'белое, розовое или игристое к рыбе'
        if flat & {'мяс', 'bbq', 'гриль'} and color == 'красное' and level in ('dry', 'medium') and not sparkling:
            return 'сухое красное к мясу'
        if 'десерт' in flat and level == 'sweet':
            return 'сладкое к десерту'
        if flat == {'сыр'} and level != 'sweet':
            return 'к сырам подходит большинство сухих'
        return ''

    def refill(self, profile, limit):
        """A bottle ran out: the same again, and the closest ones to try instead."""
        signals, virtual, labels, personal, source, conf, penalty, prior = self._context(profile)
        with self.store.db() as db:
            last = {r['slug']: r['t'] for r in db.execute(
                "SELECT slug,max(created) t FROM collection_events WHERE profile=? AND kind='open' GROUP BY slug",
                (profile,))}
        finished = [s for s in sorted(last, key=lambda s: -last[s])
                    if s in self.rec.index and not signals.get(s, {}).get('quantity')
                    and self.rec.evidence(signals.get(s, {})) >= 0][:3]
        if not finished:
            return [], 'Когда откроете последнюю бутылку, здесь появится то же вино и похожие'
        taken, out = set(signals) | self.hidden(profile), []
        for slug in finished:
            name = self.p.cards[slug]['name']
            out.append((100.0, slug, 'Закончилось — купить снова'))
            sims = self.rec.sims_to(self.rec.index[slug])
            near = sorted(((sims[j], s) for s, j in self.rec.index.items() if s not in taken and s != slug),
                          reverse=True)
            for sc, s in near[:max(2, limit // len(finished) - 1)]:
                taken.add(s)
                out.append((sc, s, f'Закончилось «{name}» — похожее'))
        chosen = self.rec.diversify(out, limit, redundancy=0.0, style_repeat=0.0, per_winery=3)
        extra = {s: {'refill_of': s} for s in finished}
        return self._cards(chosen, profile, extra), None

    def list_suggestions(self, profile, list_id, limit=None):
        """Fill a selection («подборка») for its occasion: taste + occasion fit + closeness to the
        wines already in it + range of styles; bottles the user already owns come first when they fit."""
        with self.store.db() as db:
            row = db.execute('SELECT * FROM collection_lists WHERE id=? AND profile=?', (list_id, profile)).fetchone()
            if not row:
                raise HTTPException(404, 'Подборка не найдена')
            members = {r['slug'] for r in db.execute('SELECT slug FROM collection_members WHERE list_id=?', (list_id,))}
        free = (row['size'] or 6) - len(members) if row['size'] else 6
        limit = max(1, min(limit or free or 6, 24))
        if free <= 0 and row['size']:
            return {'list_id': list_id, 'items': [], 'free': 0, 'occasion': None, 'empty': 'Все места заполнены'}
        signals, virtual, labels, personal, source, conf, penalty, prior = self._context(profile)
        text = _norm(row['name'] + ' ' + (row['goal'] or ''))
        rule = {}
        for keys, r in OCCASION_RULES:
            if any(k in text for k in keys):
                for k, v in r.items():
                    rule.setdefault(k, v)
        hidden = self.hidden(profile)
        pos = self.rec.profile(signals)[0]
        levels = {sweetness_level(self.rec.styles[i]) for i, w in pos.items() if w >= 0.5}
        guard = sum(pos.values()) >= 2 and rule.get('occasion') != 'dessert'
        # What is already on the shelf says what the shelf is about, whatever its name: the closest
        # member to every wine (cosine), and no other vintage of a wine that is already there.
        inside = [self.rec.index[s] for s in members if s in self.rec.index][:24]
        cohesion, near = [0.0] * len(self.rec.slugs), [None] * len(self.rec.slugs)
        for m in inside:
            for j, v in enumerate(self.rec.sims_to(m)):
                if v > cohesion[j]:
                    cohesion[j], near[j] = v, m
        groups = {self.rec.dupe[m] for m in inside}
        scored = []
        for slug, i in self.rec.index.items():
            if slug in members or slug in hidden or (signals.get(slug, {}).get('rating') or 3) <= 2:
                continue
            if self.rec.dupe[i] in groups:
                continue
            w, prof = self.p.cards[slug], self.rec.taste.get(slug, {})
            fit, why = 0.0, []
            sparkling = self.rec.styles[i][2]
            if rule.get('sparkling') and sparkling:
                fit += rule['sparkling'];why.append('игристое к празднику')
            dishes = _norm(' '.join(w['pairings']))
            hit = [t for t in rule.get('dish', ()) if t in dishes]
            if hit:
                fit += 1.0;why.append('к блюдам: ' + ', '.join(w['pairings'][:2]).lower())
            if rule.get('occasion') and rule['occasion'] in prof.get('occasions', []):
                fit += 0.6
                if not (sparkling and rule['occasion'] == 'celebration'):
                    why.append(OCCASIONS[rule['occasion']])
            if (rule.get('dish') or rule.get('occasion') == 'dinner') and prof.get('sweetness', 1) >= 3.5 \
                    and 'десерт' not in rule.get('dish', ()):
                fit -= 1.0  # a sweet wine with shashlik or fish is a miss, whatever the portal tags say
            if rule.get('popular'):
                fit += rule['popular'] * prior[i]
            if guard and levels and sweetness_level(self.rec.styles[i]) not in levels:
                fit -= 0.8  # the occasion does not turn a dry-wine drinker into a sweet one
            owned = signals.get(slug, {}).get('quantity', 0)
            taste = conf * personal[i] + (1 - conf) * prior[i] - 0.6 * penalty[i]
            score = 10 * taste + 6 * cohesion[i] + 4 * fit + (2 if owned else 0)
            rating = signals.get(slug, {}).get('rating')
            src = source[i]
            liked = self.rec.sim(i, src) if isinstance(src, int) and src != i else 0.0
            if rating and rating >= 4:
                why.insert(0, f'ваша оценка {rating}')
            elif cohesion[i] >= 0.45 and cohesion[i] >= liked:
                why.append('похоже на «' + self.p.cards[self.rec.slugs[near[i]]]['name'] + '» из этой коллекции')
            elif liked >= 0.4:
                why.append('похоже на «' + self.p.cards[self.rec.slugs[src]]['name'] + '»')
            reason = ('Есть у вас · ' if owned else '') + ('; '.join(why[:2]) if why else 'для разнообразия коллекции')
            scored.append((score, slug, reason[0].upper() + reason[1:]))
        chosen = self.rec.diversify(scored, limit, per_winery=2, style_repeat=0.15)
        extra = {s: {'owned': True, 'quantity': v['quantity']} for s, v in signals.items() if v.get('quantity')}
        occasion = OCCASIONS.get(rule.get('occasion')) if rule else None
        return {'list_id': list_id, 'items': self._cards(chosen, profile, extra), 'free': free, 'occasion': occasion,
                'request_id': self.log_impressions(profile, [{'slug': x[1]} for x in chosen], 'list'), 'empty': None}

    def kind_of(self, i):
        """Passport tab of catalog wine i: red / white / rose / sparkling (or None: orange, unknown)."""
        color, _, sparkling = self.rec.styles[i]
        if sparkling:
            return 'sparkling'
        return next((k for k, (_, c) in KINDS.items() if c == color), None)

    def passport(self, profile, kind=None):
        """Taste passport: averaged axes of what the user liked, top aromas, styles, grapes, regions.
        kind (red / white / rose / sparkling) narrows it to one tab, as on the profile screen."""
        if kind is not None and kind not in KINDS:
            raise HTTPException(422, 'kind: ' + ', '.join(KINDS))
        signals = self.p.taste_signals(profile)
        virtual, labels = self.onboarding_seeds(profile)
        answers = self.effective(self.answers(profile))
        quiz = answers.get('quiz') or {}
        pos, neg = self.rec.profile(signals)
        tabs = collections.Counter(self.kind_of(i) for i in pos)
        if kind:
            pos = {i: w for i, w in pos.items() if self.kind_of(i) == kind}
            neg = {i: w for i, w in neg.items() if self.kind_of(i) == kind}
            q = quiz_profile(quiz)
            wanted = KINDS[kind][1]
            virtual = [v for v in virtual if v[0] == 'onboarding:quiz' and (
                q['sparkling'] if kind == 'sparkling' else not q['colors'] or wanted in q['colors'])]
        total = sum(pos.values())
        evidence = total + 0.5 * sum(neg.values()) + sum(w for _, _, w in virtual)
        stage = 'new' if not evidence else 'learning' if evidence < 3 else 'confident'
        out = {'stage': stage, 'evidence': round(evidence, 2), 'liked_count': len(pos), 'disliked_count': len(neg),
               'axes': [], 'aromas': [], 'styles': [], 'grapes': [], 'regions': [], 'colors': [], 'summary': None,
               'onboarding': bool(virtual), 'quiz': quiz, 'kind': kind,
               'tabs': [{'id': k, 'label': label, 'count': tabs[k]} for k, (label, _) in KINDS.items()],
               'swiped': len(self.swipes(profile))}
        weights = {self.rec.slugs[i]: w for i, w in pos.items()}
        sources = [(self.rec.taste.get(s, {}), w) for s, w in weights.items()]
        colors = collections.Counter()
        for i, w in pos.items():
            if self.rec.styles[i][0]:
                colors[self.rec.styles[i][0]] += w
        for label, _, w in virtual:
            key = label.split(':', 1)[1]
            if key == 'quiz':
                q = quiz_profile(quiz)
                sources.append(({**q['taste'], 'aromas': q['aromas']}, w))
                for c in q['colors']:
                    colors[c] += w / len(q['colors'])
            elif TILES.get(key):
                tile = TILES[key]
                sources.append(({**tile.get('taste', {}), 'aromas': list(tile.get('aromas', ()))}, w))
        out['colors'] = [c for c, _ in colors.most_common()]
        if not sources:
            return out
        for axis in AXES:
            if axis == 'tannin' and kind in ('white', 'rose', 'sparkling'):
                continue  # almost no tannin there: an average would only describe the reds
            vals = [(p[axis], w) for p, w in sources if isinstance(p.get(axis), (int, float))]
            if vals:
                v = sum(x * w for x, w in vals) / sum(w for _, w in vals)
                out['axes'].append({'axis': axis, 'name': AXIS_NAMES[axis], 'value': round(v, 1)})
        aromas = collections.Counter()
        for p, w in sources:
            for a in p.get('aromas', []):
                aromas[a] += w
        norm = sum(w for _, w in sources)
        out['aromas'] = [{'id': a, 'name': AROMAS[a], 'share': round(v / norm, 2)} for a, v in aromas.most_common(4)]

        def top(values, n):
            c = collections.Counter()
            for value, w in values:
                if value:
                    c[value] += w
            t = sum(c.values()) or 1
            return [{'name': k, 'share': round(v / t, 2)} for k, v in c.most_common(n)]
        out['styles'] = top([(self.p.cards[s]['category'], w) for s, w in weights.items()], 3)
        out['grapes'] = top([(g.strip(), w) for s, w in weights.items() for g in self.p.cards[s]['grapes'].split(',')], 3)
        out['regions'] = top([(self.p.cards[s]['region'], w) for s, w in weights.items()], 2)
        out['summary'] = self._summary(out)
        return out

    @staticmethod
    def _summary(p):
        axes = {a['axis']: a['value'] for a in p['axes']}
        if not axes:
            return None
        body = axes.get('body', 3)
        sweet = axes.get('sweetness', 1)
        words = ['полнотелые' if body >= 3.8 else 'лёгкие' if body <= 2.3 else 'средние по телу']
        words.append('сладкие' if sweet >= 4 else 'полусладкие' if sweet >= 3 else 'сухие')
        if p.get('kind'):
            style = 'игристое' if p['kind'] == 'sparkling' else KINDS[p['kind']][1]
        else:
            style = (p['styles'][0]['name'].split()[0].lower() if p['styles'] else
                     p['colors'][0] if len(p.get('colors') or []) == 1 else '')
        if style:
            words.append(style.replace('ое', 'ые') if style.endswith('ое') else style)
        line = 'Вы любите ' + ' '.join(words) + ' вина'
        if p['aromas']:
            line += ' с нотами: ' + ', '.join(a['name'] for a in p['aromas'][:2])
        return line

    # ---- taste quiz --------------------------------------------------------------------
    def quiz_options(self, profile):
        questions = [{'id': q['id'], 'title': q['title'], 'options': [
            {k: o[k] for k in ('id', 'label', 'icon', 'insight')} for o in q['options']]} for q in QUIZ]
        return {'questions': questions, 'answers': (self.answers(profile) or {}).get('quiz') or {}}

    def save_quiz(self, profile, body):
        """Save quiz answers ({question: option}; skipped questions are left out). Colour, sweetness and
        sparkling are derived here, so the feed filters and seeds work as for the old onboarding."""
        given = body.get('answers') if isinstance(body, dict) else None
        if not isinstance(given, dict) or any((q, o) not in QUIZ_OPTIONS for q, o in given.items()):
            raise HTTPException(422, 'answers: {вопрос: вариант} из списка анкеты')
        prof = quiz_profile(given)
        answers = {k: v for k, v in (self.answers(profile) or {}).items() if k in ('dishes', 'tiles')}
        answers.update(quiz=given, colors=prof['colors'], sweetness=prof['sweetness'])
        with self.store.db() as db:
            db.execute('INSERT INTO recsys_onboarding VALUES(?,?,?) ON CONFLICT(profile) DO UPDATE SET '
                       'answers=excluded.answers,updated=excluded.updated',
                       (profile, json.dumps(answers, ensure_ascii=False), time.time()))
        return {'ok': True, 'answers': given, 'passport': self.passport(profile),
                'recommendations': self.endpoint(profile, 'for_you', '', 8)}

    # ---- taste of one wine ---------------------------------------------------------------
    def _me(self, profile):
        """The user's taste in the shape wine_taste compares against (None for a newcomer)."""
        p = self.passport(profile)
        if p['stage'] == 'new' or not p['axes']:
            return None
        return {'axes': {a['axis']: a['value'] for a in p['axes']}, 'aromas': [a['id'] for a in p['aromas']],
                'colors': p['colors']}

    def wine_taste(self, slug, profile=None, me=False):
        """Taste sheet of a wine: scales (Vivino-style), aroma groups, occasions, and how well it
        matches the user's passport. `me` may be passed in when sheets are built for a whole deck."""
        i = self.rec.index[slug]
        prof = self.rec.taste.get(slug, {})
        color, _, sparkling = self.rec.styles[i]
        red = color == 'красное' and not sparkling
        scales = [{'axis': a, 'name': AXIS_NAMES[a], 'low': low, 'high': high, 'value': prof[a]}
                  for a, low, high in SCALES if isinstance(prof.get(a), (int, float)) and (a != 'tannin' or red)]
        notes = []
        if sparkling:
            notes.append('Игристое')
        if (prof.get('oak') or 0) >= 3.5:
            notes.append('Выдержка в дубе')
        out = {'scales': scales, 'aromas': [{'id': a, 'name': AROMAS[a]} for a in prof.get('aromas', []) if a in AROMAS],
               'occasions': [OCCASIONS[o] for o in prof.get('occasions', []) if o in OCCASIONS], 'notes': notes,
               'source': ('По описанию винодельни' if prof.get('source') == 'model' else
                          'По описанию, стилю и сорту винограда'), 'match': None}
        if me is False:
            me = self._me(profile) if profile else None
        if not me:
            return out
        diffs = {s['axis']: s['value'] - me['axes'][s['axis']] for s in scales if s['axis'] in me['axes']}
        if not diffs:
            return out
        axis_fit = sum(1 - min(abs(d), 4) / 4 for d in diffs.values()) / len(diffs)
        wine_aromas = [a['id'] for a in out['aromas']]
        aroma_fit = (len(set(wine_aromas) & set(me['aromas'][:4])) / min(len(wine_aromas), 2)
                     if wine_aromas and me['aromas'] else 0.5)
        color_fit = 1.0 if not me['colors'] or color in me['colors'][:2] else 0.4
        raw = 0.6 * axis_fit + 0.25 * min(aroma_fit, 1.0) + 0.15 * color_fit
        # Stretch: a near-identical structure reads 95+, a wine of the opposite style falls below 50.
        percent = max(10, min(99, round(100 * (raw - 0.45) / 0.5)))
        your = {a: me['axes'][a] for a in diffs}
        far = sorted((d for d in diffs.items() if abs(d[1]) >= 1.25), key=lambda d: -abs(d[1]))
        near = [AXIS_GENITIVE[a] for a, d in diffs.items() if abs(d) <= 0.6]
        if far:
            words = [AXIS_WORDS[a][0 if d > 0 else 1] for a, d in far[:2]]
            note = (' и '.join(words)).capitalize() + ', чем вы обычно любите'
        elif near:
            note = 'Совпадает с вашим вкусом по ' + (', '.join(near[:-1]) + ' и ' + near[-1] if len(near) > 1 else near[0])
        else:
            note = 'Близко к вашему вкусу'
        common = [AROMAS[a] for a in wine_aromas if a in me['aromas'][:4]]
        if common and not far:
            note += '; ваши ноты: ' + ', '.join(common[:2])
        out['match'] = {'percent': percent, 'note': note, 'yours': your}
        return out

    # ---- swipe deck («тиндер по винам») --------------------------------------------------
    def deck(self, profile, limit=10):
        """Next wines to swipe: the feed's taste-ranked, diversified picks without already swiped ones,
        each with its taste sheet. Swipes sharpen the passport faster than browsing does."""
        if type(limit) is not int or not 1 <= limit <= 24:
            raise HTTPException(422, 'limit от 1 до 24')
        swiped = self.swipes(profile)
        items = [w for w in self.p.recommendations(profile, '', limit, exclude=set(swiped)) if w['slug'] not in swiped]
        me = self._me(profile)
        items = [{**w, 'taste': self.wine_taste(w['slug'], me=me)} for w in items]
        return {'items': items, 'request_id': self.log_impressions(profile, items, 'swipe'),
                'swiped': len(swiped), 'liked': sum(v == 'like' for v in swiped.values()),
                'empty': None if items else 'Вы пролистали все подходящие вина — загляните позже'}

    def swipe(self, profile, body):
        """like — «звучит вкусно»: weak liking, and the wine goes to «Лайки» to try later;
        nope — «не моё»: weak dislike, hidden from the feed; skip — just next; undo — as if never swiped."""
        if not isinstance(body, dict):
            raise HTTPException(422, 'Ожидается объект')
        slug, verdict = body.get('slug'), body.get('verdict')
        self.p.require_slug(slug)
        if verdict not in VERDICTS + ('undo',):
            raise HTTPException(422, 'verdict: like, nope, skip или undo')
        with self.store.db() as db:
            row = db.execute('SELECT favorited FROM recsys_swipes WHERE profile=? AND slug=?', (profile, slug)).fetchone()
            if row and row['favorited']:  # a repeated or undone swipe takes back the like it added
                db.execute('UPDATE wine_preferences SET favorite=0,updated=? WHERE profile=? AND slug=?',
                           (time.time(), profile, slug))
            db.execute('DELETE FROM recsys_swipes WHERE profile=? AND slug=?', (profile, slug))
            if verdict != 'undo':
                favorited = 0
                if verdict == 'like':
                    pref = db.execute('SELECT favorite FROM wine_preferences WHERE profile=? AND slug=?',
                                      (profile, slug)).fetchone()
                    if not (pref and pref['favorite']):
                        db.execute('INSERT OR IGNORE INTO wine_preferences VALUES(?,?,0,0,?)', (profile, slug, time.time()))
                        db.execute('UPDATE wine_preferences SET favorite=1,updated=? WHERE profile=? AND slug=?',
                                   (time.time(), profile, slug))
                        favorited = 1
                db.execute('INSERT INTO recsys_swipes VALUES(?,?,?,?,?)', (profile, slug, verdict, favorited, time.time()))
        return {'ok': True, 'passport': self.passport(profile)}

    def merge_guest(self, db, source, target):
        """Called inside Collection.merge_guest: keep a guest's onboarding and feedback after sign-in."""
        db.execute('UPDATE recsys_events SET profile=? WHERE profile=?', (target, source))
        if not db.execute('SELECT 1 FROM recsys_onboarding WHERE profile=?', (target,)).fetchone():
            db.execute('UPDATE recsys_onboarding SET profile=? WHERE profile=?', (target, source))
        db.execute('DELETE FROM recsys_onboarding WHERE profile=?', (source,))
        db.execute('INSERT OR IGNORE INTO recsys_swipes SELECT ?,slug,verdict,favorited,created FROM recsys_swipes '
                   'WHERE profile=?', (target, source))
        db.execute('DELETE FROM recsys_swipes WHERE profile=?', (source,))

    def routes(self, app):
        from fastapi import Request

        @app.get('/api/product/taste')
        def taste(request: Request, kind: str = ''):
            return self.passport(request.state.profile, kind or None)

        @app.get('/api/product/taste/quiz')
        def quiz_options(request: Request):
            return self.quiz_options(request.state.profile)

        @app.post('/api/product/taste/quiz')
        def quiz(body: dict, request: Request):
            return self.save_quiz(request.state.profile, body)

        @app.get('/api/product/taste/swipe')
        def deck(request: Request, limit: int = 10):
            return self.deck(request.state.profile, limit)

        @app.post('/api/product/taste/swipe')
        def swipe(body: dict, request: Request):
            return self.swipe(request.state.profile, body)

        @app.get('/api/product/taste/onboarding')
        def onboarding_options(request: Request):
            return self.onboarding_options(request.state.profile)

        @app.post('/api/product/taste/onboarding')
        def onboarding(body: dict, request: Request):
            return self.save_onboarding(request.state.profile, body)

        @app.post('/api/product/recommendations/feedback')
        def feedback(body: dict, request: Request):
            return self.feedback(request.state.profile, body)

        @app.get('/api/product/collection/lists/{list_id}/suggestions')
        def suggestions(list_id: str, request: Request, limit: int = 0):
            return self.list_suggestions(request.state.profile, list_id, limit or None)
