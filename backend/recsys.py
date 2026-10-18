"""Content-based recommender over the catalog, personalised by the user's collection.

No interaction logs exist yet, so nothing here is learned from users: every wine is a sparse
vector of what the portal says about it (style, grapes, tasting-note words, region, producer,
strength, serving temperature, food pairings), and a user is the set of wines they have
rated, bought, opened, saved or scanned, each with a weight. A candidate's personal score is
its similarity to the closest liked wine (max, not mean: someone who likes dry reds and brut
must not be averaged into semi-sweet rosé), minus its similarity to wines rated 1-2.

Pure Python (the app server installs no numpy); an inverted index keeps a request at a few
milliseconds for the 2103-wine catalog.
"""
import collections
import math
import re

from taste import AXES, load_profiles

COLORS = (('красн', 'красное'), ('бел', 'белое'), ('розов', 'розовое'), ('оранж', 'оранжевое'))
SWEETNESS = (('экстра брют', 'экстра брют'), ('брют', 'брют'), ('полусух', 'полусухое'),
             ('полуслад', 'полусладкое'), ('сух', 'сухое'), ('слад', 'сладкое'))
STOP = set('''вино вина вину вином вкус вкусе вкуса аромат аромате аромата ароматы ароматом
нотки ноты нотами нотах оттенки оттенками тона тонами также который которое которая очень
этого этот этом более менее легкий легкие легкая легкое приятный приятное приятная нежный
хорошо хорошая хороший имеет присутствует присутствуют раскрывается раскрываются переходит
послевкусие послевкусии послевкусием долгое долгим длительное средней средняя среднее цвет
цвета цветом гармоничный гармоничное гармоничная сбалансированный сбалансированное
выдержки выдержка выдержано дубовых бочках бочке бочках месяцев лет года году сорта сорт
винограда виноград урожая урожай рекомендуется подавать подходит идеально отлично
сочетается сочетании сочетание блюдами блюдам блюд'''.split())

# How much each block of features counts in the similarity. Tuned on the pairing hold-out
# (ml/recsys/eval_pairing.py); dishes are left out there so the check is not circular.
BLOCKS = {'style': 1.0, 'grape': 0.6, 'text': 1.0, 'dish': 0.6, 'region': 0.2,
          'winery': 0.25, 'body': 0.35, 'taste': 0.4, 'aroma': 0.3}

# Descriptor stems worth quoting back to the user when explaining a match ("черника, ваниль").
AROMAS = tuple('''вишн череш слив смород ежеви малин клубн земля черни клюкв гранат инжир черносл изюм
цитрус лимон лайм грейп апельс манда персик абрик груш яблок айв дын анана манго маракуй банан
личи крыжов роз фиалк жасми акаци липов бузин мед мёд ванил кориц гвоздик перц пряно табак кож
кофе шокол какао карам орех минда фунду тост бриош хлеб дуб дым минер кремн трав мят эвкал хво
гриб бархат танин кисло полнот насыщ сливоч'''.split())

# Weight of each collection signal as evidence of taste. A rating decides on its own when
# present; otherwise a bottle opened or kept counts, saving to the wishlist and scanning do not.
RATING_WEIGHT = {5: 1.0, 4: 0.7, 3: 0.15, 2: -0.6, 1: -1.0}


def taste_features(taste):
    """Each 1-5 axis as soft low/mid/high buckets, so cosine compares structure, not just size."""
    out = {}
    for axis in AXES:
        v = taste.get(axis)
        if not isinstance(v, (int, float)):
            continue
        for name, centre in (('lo', 1.5), ('mid', 3.0), ('hi', 4.5)):
            w = max(0.0, 1 - abs(v - centre) / 1.5)
            if w:
                out[f'taste:{axis}:{name}'] = w
    return out


# Catalog placeholders, not varieties: they must not make two wines look alike or 'new'.
GENERIC_GRAPES = {'белые сорта винограда', 'красные сорта винограда', 'розовые сорта винограда', 'купаж',
                  'ассамбляж', 'белые сорта', 'красные сорта'}


def grapes_of(card):
    return [g for g in (x.strip().lower() for x in str(card.get('grapes', '')).split(','))
            if g and g not in GENERIC_GRAPES]


def sweetness_level(style):
    """dry / medium / sweet — brut and extra brut count as dry."""
    sweet = style[1]
    if not sweet:
        return 'unknown'  # «Красное» without sweetness: often a cheap semi-sweet, never assume dry
    return 'sweet' if sweet in ('полусладкое', 'сладкое') else 'medium' if sweet == 'полусухое' else 'dry'


def norm(text):
    return re.sub(r'[^а-яa-z0-9]+', ' ', str(text or '').lower().replace('ё', 'е')).strip()


def style_of(card):
    text = norm(card.get('category', '') + ' ' + card.get('name', ''))
    color = next((c for root, c in COLORS if root in text), '')
    sweet = next((s for root, s in SWEETNESS if root in text), '')
    sparkling = 'брют' in sweet or 'игрист' in text
    return color, sweet, sparkling


def stem(word):
    return word[:5] if len(word) > 5 else word


def first_number(value):
    m = re.search(r'\d+(?:[.,]\d+)?', str(value or ''))
    return float(m.group().replace(',', '.')) if m else None


def dedupe_key(card):
    name = re.sub(r'\b(19|20)\d\d\b|\d+[.,]?\d*\s*%?', ' ', norm(card.get('name', '')))
    return (norm(card.get('winery', '')), ' '.join(name.split()))


class Recommender:
    def __init__(self, cards, blocks=None, use_dishes=True, profiles=None):
        self.blocks = dict(BLOCKS, **(blocks or {}))
        if not use_dishes:
            self.blocks['dish'] = 0.0
        self.slugs = list(cards)
        self.taste = profiles if profiles is not None else load_profiles(cards)
        self.index = {s: i for i, s in enumerate(self.slugs)}
        self.cards = cards
        self.styles = [style_of(cards[s]) for s in self.slugs]
        self.dupe = [dedupe_key(cards[s]) for s in self.slugs]
        self.main_grape = [(grapes_of(cards[s]) or [''])[0] for s in self.slugs]
        words = [[w for w in norm(cards[s].get('description', '')).split()
                  if len(w) > 3 and w not in STOP and not w.isdigit()] for s in self.slugs]
        df = collections.Counter(t for ws in words for t in {stem(w) for w in ws})
        n = max(len(self.slugs), 1)
        self.idf = {t: math.log(n / c) for t, c in df.items() if c >= 2 and c <= 0.25 * n}
        spelled = collections.defaultdict(collections.Counter)
        for ws in words:
            for w in ws:
                spelled[stem(w)][w] += 1
        self.word = {t: c.most_common(1)[0][0] for t, c in spelled.items()}
        self.vectors = [self._vector(cards[s], self.styles[i], words[i], self.taste.get(s))
                        for i, s in enumerate(self.slugs)]
        self.postings = collections.defaultdict(list)
        for i, v in enumerate(self.vectors):
            for f, x in v.items():
                self.postings[f].append((i, x))

    def _vector(self, card, style, words, taste=None):
        color, sweet, sparkling = style
        blocks = {'style': {}, 'grape': {}, 'text': {}, 'dish': {}, 'region': {}, 'winery': {},
                  'body': {}, 'taste': {}, 'aroma': {}}
        if taste:
            blocks['taste'] = taste_features(taste)
            for a in taste.get('aromas') or []:
                blocks['aroma']['aroma:' + a] = 1.0
        if color:
            blocks['style']['color:' + color] = 1.0
        if sweet:
            blocks['style']['sweet:' + sweet] = 0.8
        if sparkling:
            blocks['style']['sparkling'] = 0.8
        grapes = grapes_of(card)
        for g in grapes:
            blocks['grape']['grape:' + g] = 1.0 / math.sqrt(len(grapes))
        tf = collections.Counter(stem(w) for w in words)
        for t, c in tf.items():
            if t in self.idf:
                blocks['text']['text:' + t] = (1 + math.log(c)) * self.idf[t]
        for d in card.get('pairings') or []:
            blocks['dish']['dish:' + d.lower()] = 1.0
        if card.get('region'):
            blocks['region']['region:' + norm(card['region'])] = 1.0
        if card.get('winery'):
            blocks['winery']['winery:' + norm(card['winery'])] = 1.0
        alcohol = first_number(card.get('alcohol'))
        if alcohol:
            blocks['body']['alc:' + ('light' if alcohol < 11.5 else 'mid' if alcohol < 13.5 else 'full')] = 1.0
        temp = first_number(card.get('temperature'))
        if temp:
            blocks['body']['temp:' + ('cold' if temp < 9 else 'cool' if temp < 14 else 'room')] = 0.7
        return self._combine(blocks)

    def _combine(self, blocks):
        vec = {}
        for name, feats in blocks.items():
            weight = self.blocks.get(name, 0.0)
            length = math.sqrt(sum(x * x for x in feats.values()))
            if weight and length:
                for f, x in feats.items():
                    vec[f] = weight * x / length
        length = math.sqrt(sum(x * x for x in vec.values())) or 1.0
        return {f: x / length for f, x in vec.items()}

    def sim(self, i, j):
        a, b = self.vectors[i], self.vectors[j]
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(f, 0.0) for f, x in a.items())

    def sims_to(self, i):
        """Cosine similarity of wine i to every wine, via the inverted index."""
        return self.sims_vec(self.vectors[i])

    def sims_vec(self, vector, partial=False):
        """Cosine of `vector` with every wine. partial=True compares only on the kinds of features
        the vector has (an onboarding answer says nothing about grapes or producers, so a wine is
        not penalised for having them)."""
        out = [0.0] * len(self.slugs)
        for f, x in vector.items():
            for j, y in self.postings.get(f, ()):
                out[j] += x * y
        if partial:
            kinds = {f.split(':', 1)[0] for f in vector}
            for j, v in enumerate(self.vectors):
                sub = math.sqrt(sum(y * y for f, y in v.items() if f.split(':', 1)[0] in kinds))
                out[j] = out[j] / sub if sub else 0.0
        return out

    def virtual_vector(self, colors=(), sweets=(), sparkling=False, taste=None, aromas=(), dishes=()):
        """A wine that does not exist: what an onboarding answer describes, in the same space."""
        blocks = {'style': {}, 'taste': {}, 'aroma': {}, 'dish': {}}
        for c in colors:
            blocks['style']['color:' + c] = 1.0
        for sw in sweets:
            blocks['style']['sweet:' + sw] = 0.8
        if sparkling:
            blocks['style']['sparkling'] = 0.8
        if taste:
            blocks['taste'] = taste_features(taste)
        for a in aromas:
            blocks['aroma']['aroma:' + a] = 1.0
        for d in dishes:
            blocks['dish']['dish:' + d.lower()] = 1.0
        return self._combine(blocks)

    # ---- users -------------------------------------------------------------------------
    @staticmethod
    def evidence(sig):
        """Taste evidence of one collection entry, in [-1, 1]."""
        rating = sig.get('rating') or 0
        if rating:
            return RATING_WEIGHT[rating]
        w = 0.0
        if sig.get('opened'):
            w += 0.45 + 0.1 * min(sig['opened'] - 1, 2)
        if sig.get('quantity'):
            w += 0.35
        if sig.get('tried'):
            w += 0.25
        if sig.get('not_for_me'):  # «не то» on a recommendation: a mild dislike
            return -0.5
        # Wishlist, scans and bottles given away are intent, not taste: they add nothing
        # (product rule, see test_wishlist_does_not_train_taste).
        return min(w, 0.9)

    def profile(self, signals):
        pos, neg = {}, {}
        for slug, sig in signals.items():
            i = self.index.get(slug)
            if i is None:
                continue
            w = self.evidence(sig)
            if w > 0:
                pos[i] = w
            elif w < 0:
                neg[i] = -w
        return pos, neg

    def personal(self, signals, max_seeds=40, virtual=()):
        """Per-wine personal score, what explains it (a liked wine's index, or the label of an
        onboarding answer), confidence in [0, 1) that grows with evidence, and similarity to
        disliked wines (to keep exploration away from them).

        `virtual`: (label, vector, weight) seeds from onboarding; they count as evidence too, so
        their influence fades as real ratings arrive."""
        pos, neg = self.profile(signals)
        seeds = [(i, w, self.vectors[i]) for i, w in sorted(pos.items(), key=lambda kv: -kv[1])[:max_seeds]]
        seeds += [(label, w, vec) for label, vec, w in virtual]
        n = len(self.slugs)
        best, second, source = [0.0] * n, [0.0] * n, [None] * n
        for i, w, vec in seeds:
            s = self.sims_vec(vec, partial=isinstance(i, str))
            for j in range(n):
                v = w * s[j]
                if v > best[j]:
                    second[j], best[j], source[j] = best[j], v, i
                elif v > second[j]:
                    second[j] = v
        penalty = [0.0] * n
        for i, w in sorted(neg.items(), key=lambda kv: -kv[1])[:max_seeds]:
            s = self.sims_to(i)
            for j in range(n):
                penalty[j] = max(penalty[j], w * s[j])
        score = [max(0.0, best[j] + 0.25 * second[j] - 0.8 * penalty[j]) for j in range(n)]
        evidence = sum(pos.values()) + 0.5 * sum(neg.values()) + sum(w for _, _, w in virtual)
        confidence = evidence / (evidence + 1.5)
        return score, source, confidence, penalty

    def prior(self, ratings, scans):
        """Popularity among real users (Bayesian-shrunk rating, scan volume); 0 on a fresh base."""
        count = sum(r['rating_count'] for r in ratings.values())
        mean = (sum(r['average_rating'] * r['rating_count'] for r in ratings.values()) / count
                if count else 3.5)
        top_scans = max(scans.values(), default=0)
        out = []
        for s in self.slugs:
            r = ratings.get(s)
            stars = ((r['average_rating'] * r['rating_count'] + 3 * mean) / (r['rating_count'] + 3)
                     if r else mean)
            rating_part = (stars - mean) / 2.5 if r else 0.0
            scan_part = math.log1p(scans.get(s, 0)) / math.log1p(top_scans) if top_scans else 0.0
            card = self.cards[s]
            complete = 0.05 * bool(card.get('description')) + 0.05 * bool(card.get('pairings'))
            out.append(max(0.0, 0.5 * scan_part + 0.4 * rating_part) + complete)
        return out

    # ---- ranking -----------------------------------------------------------------------
    def interest(self, i):
        """Coarse taste 'interest' of a wine: colour, still/sparkling, dry/sweet. An onboarding
        answer is an interest of its own."""
        if isinstance(i, str):
            return ('onboarding', i)
        color, sweet, sparkling = self.styles[i]
        return (color, sparkling, sweet in ('полусладкое', 'сладкое'))

    def style_shares(self):
        counts = collections.Counter(self.interest(i) for i in range(len(self.slugs)))
        total = sum(math.sqrt(c) for c in counts.values()) or 1.0
        return {k: math.sqrt(c) / total for k, c in counts.items()}

    def diversify(self, scored, limit, per_winery=2, redundancy=0.35, style_repeat=0.1,
                  interests=None, shares=None, grape_repeat=0.12):
        """Pick `limit` wines from (score, slug, reason) tuples.

        MMR inside: trade a little score for not showing near-copies. Across interests: when the
        user has several (Saperavi *and* brut), slots go to them in proportion to how much
        evidence each has (D'Hondt), so the strongest one cannot take the whole feed; wines
        explained by no liked wine form an 'explore' interest. Without interests, repeating a
        style is penalised instead (cold start: show the range). One wine per duplicate group,
        at most `per_winery` per producer unless the query leaves no choice."""
        ranked = sorted(scored, key=lambda x: -x[0])
        if interests:  # the best of every interest must reach the pool, not only of the top one
            per_key = collections.Counter()
            pool = []
            for item in ranked:
                k = interests.get(item[1])
                if per_key[k] < max(limit * 15, 60):
                    per_key[k] += 1
                    pool.append(item)
        else:
            pool = ranked[:max(limit * 40, 300)]
        chosen, wineries, groups, capped = [], collections.Counter(), set(), []
        styles, taken, grapes = collections.Counter(), collections.Counter(), collections.Counter()
        overlap = [0.0] * len(pool)  # max similarity of each pool item to anything chosen so far
        alive = list(range(len(pool)))
        key = (lambda t: interests.get(pool[t][1])) if interests else (lambda t: None)
        while alive and len(chosen) < limit:
            if interests:
                open_keys = {key(t) for t in alive}
                best_key = max(open_keys, key=lambda k: (shares.get(k, 0.0) / (taken[k] + 1), str(k)))
                candidates = [t for t in alive if key(t) == best_key]
            else:
                candidates = alive

            def gain(t):
                i = self.index[pool[t][1]]
                repeat = 0.0 if interests else style_repeat * styles[self.styles[i]]
                if self.main_grape[i]:  # six Saperavi in a row is a list, not a recommendation
                    repeat += grape_repeat * grapes[self.main_grape[i]]
                return pool[t][0] * (1 - redundancy * overlap[t] - repeat)
            t = max(candidates, key=gain)
            alive.remove(t)
            item = pool[t]
            i = self.index[item[1]]
            winery = self.dupe[i][0]
            if self.dupe[i] in groups:
                continue
            if winery and wineries[winery] >= per_winery:
                capped.append(item)
                continue
            chosen.append(item)
            groups.add(self.dupe[i])
            wineries[winery] += 1
            styles[self.styles[i]] += 1
            grapes[self.main_grape[i]] += 1
            taken[key(t)] += 1
            for u in alive:
                overlap[u] = max(overlap[u], self.sim(i, self.index[pool[u][1]]))
        for item in capped:  # a narrow query may only match one producer: fill rather than come back short
            if len(chosen) >= limit:
                break
            if self.dupe[self.index[item[1]]] not in groups:
                chosen.append(item)
                groups.add(self.dupe[self.index[item[1]]])
        return chosen

    def similarity_to_source(self, j, i, virtual=()):
        if isinstance(i, str):
            vec = next((v for label, v, _ in virtual if label == i), {})
            kinds = {f.split(':', 1)[0] for f in vec}
            a = self.vectors[j]
            sub = math.sqrt(sum(y * y for f, y in a.items() if f.split(':', 1)[0] in kinds))
            return sum(x * a.get(f, 0.0) for f, x in vec.items()) / sub if sub else 0.0
        return self.sim(j, i)

    def why(self, j, i, signals, labels=None):
        """One-line reason that candidate j fits, in terms of the liked wine i (or answer i)."""
        if isinstance(i, str):
            return 'Под ваш вкус: ' + (labels or {}).get(i, i).lower()
        liked = self.cards[self.slugs[i]]
        sig = signals.get(self.slugs[i], {})
        how = (f'ваша оценка {sig["rating"]}' if sig.get('rating') else
               'вы его открывали' if sig.get('opened') else
               'есть в коллекции' if sig.get('quantity') else
               'в избранном' if sig.get('favorite') else 'вы его сканировали')
        a, b = self.vectors[j], self.vectors[i]
        common = sorted(((a[f] * b[f], f) for f in a if f in b), reverse=True)
        traits, region = [], ''
        for _, f in common:
            kind, _, value = f.partition(':')
            if kind == 'grape':
                traits.append('сорт ' + value.title())
            elif kind == 'text' and value in self.word and value.startswith(AROMAS):
                traits.append(self.word[value])
            elif kind == 'region' and not region:
                region = self.cards[self.slugs[j]].get('region', '')
            if len(traits) == 3:
                break
        if len(traits) < 2 and region:  # the region only when there is little else to say
            traits.append(region)
        line = f'Похоже на «{liked.get("name", "")}» ({how})'
        return line + (': ' + ', '.join(t for t in traits if t) if traits else '')
