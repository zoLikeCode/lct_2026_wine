"""Taste profile of a wine: five axes on a 1-5 scale plus aroma groups and occasions.

Two sources, best first:
1. content/wine_taste.json — extracted once from the portal description by the sommelier
   model (ml/recsys/extract_taste_gigachat.py); a null field means the text does not say.
2. rules here — category for sweetness, cue words in the description, and typical profiles
   of grape varieties for whatever the text leaves out.

A field the model left null falls back to the rules, so every wine has a full profile and
`source` says where each one came from.
"""
import json
import re
from pathlib import Path

AXES = ('body', 'acidity', 'tannin', 'sweetness', 'oak')
AXIS_NAMES = {'body': 'Тело', 'acidity': 'Кислотность', 'tannin': 'Танины', 'sweetness': 'Сладость',
              'oak': 'Дуб и выдержка'}
AROMAS = {
    'red_berries': 'красные ягоды', 'dark_berries': 'тёмные ягоды', 'citrus': 'цитрусы',
    'orchard': 'яблоко, груша, косточковые', 'tropical': 'тропические фрукты', 'floral': 'цветы',
    'herbal': 'травы и зелень', 'spice': 'пряности', 'oak_vanilla': 'дуб, ваниль, тост',
    'honey_dried': 'мёд и сухофрукты', 'mineral': 'минеральность', 'earthy': 'кожа, табак, земля',
    'pastry': 'бриошь, выпечка, сливки',
}
OCCASIONS = {
    'aperitif': 'аперитив', 'celebration': 'праздник', 'dinner': 'ужин', 'picnic': 'пикник и лето',
    'dessert': 'к десерту', 'everyday': 'на каждый день', 'special': 'особый случай',
}

# Onboarding tiles: a newcomer picks what sounds good; each becomes a virtual liked wine.
TILES = {
    'light_fresh': {'label': 'Лёгкое и свежее', 'taste': {'body': 2, 'acidity': 4.5, 'tannin': 1, 'oak': 1},
                    'aromas': ('citrus', 'orchard', 'mineral')},
    'fruity_soft': {'label': 'Фруктовое и мягкое', 'taste': {'body': 3, 'acidity': 3, 'tannin': 2.5, 'oak': 1.5},
                    'aromas': ('red_berries', 'orchard')},
    'rich_oak': {'label': 'Насыщенное, с дубом', 'taste': {'body': 4.5, 'acidity': 3, 'tannin': 4, 'oak': 4.5},
                 'aromas': ('dark_berries', 'oak_vanilla', 'spice')},
    'aromatic': {'label': 'Ароматное, цветочное', 'taste': {'body': 3, 'acidity': 3, 'tannin': 1, 'oak': 1},
                 'aromas': ('floral', 'tropical', 'honey_dried')},
    'dessert': {'label': 'Сладкое, десертное', 'taste': {'sweetness': 5, 'body': 3.5, 'tannin': 1},
                'aromas': ('honey_dried', 'floral')},
    'bubbles': {'label': 'Игристое', 'taste': {'body': 2.5, 'acidity': 4.5, 'tannin': 1}, 'aromas': ('pastry', 'citrus'),
                'sparkling': True},
}

# Taste quiz: six everyday questions, Vivino-style. Each answer nudges the taste axes (1-5), aroma
# groups, colour and sweetness; together they become one virtual liked wine (recsys_service).
# 'insight' is shown under the chosen option: what the answer tells us about wine.
QUIZ = [
    {'id': 'wine', 'title': 'Что вы обычно выбираете, когда пьёте вино?', 'options': [
        {'id': 'red', 'label': 'Чаще красное', 'icon': 'glass-red', 'colors': ['красное'],
         'insight': 'Вам близки ягодная глубина и структура. С красных и начнём, а белые покажем, '
                    'когда они точно попадут во вкус.'},
        {'id': 'white', 'label': 'Чаще белое или розовое', 'icon': 'glass-white', 'colors': ['белое', 'розовое'],
         'insight': 'Свежесть, фрукты и лёгкость — ваша территория. Подберём белые и розовые, '
                    'в которых хочется сделать ещё глоток.'},
        {'id': 'both', 'label': 'И то, и другое — ещё исследую', 'icon': 'glass-both',
         'insight': 'Отлично: не будем ограничивать цвет. Ориентироваться будем на вкус — '
                    'его расскажут следующие ответы.'},
    ]},
    {'id': 'coffee', 'title': 'Как вы пьёте кофе?', 'options': [
        {'id': 'black', 'label': 'Чёрный, без добавок', 'icon': 'cup-black', 'taste': {'tannin': 4, 'body': 4},
         'insight': 'Терпкость вас не пугает. В вине за неё отвечают танины — та же сухая, '
                    'вяжущая грань, что у крепкого эспрессо.'},
        {'id': 'milk', 'label': 'С молоком или сахаром', 'icon': 'cup-milk', 'taste': {'tannin': 2.5},
         'insight': 'Вам приятнее, когда горчинка смягчена. Подберём вина с мягкими, '
                    'бархатными танинами — без вяжущей сухости.'},
        {'id': 'none', 'label': 'Не пью кофе', 'icon': 'cup-empty', 'taste': {'tannin': 2},
         'insight': 'Тогда начнём с мягких вин, а терпкость уточним по вашим оценкам — '
                    'профиль подстроится сам.'},
    ]},
    {'id': 'chocolate', 'title': 'Какой шоколад вы бы выбрали?', 'options': [
        {'id': 'dark', 'label': 'Тёмный, от 70%', 'icon': 'choc-dark', 'taste': {'body': 4.5, 'tannin': 4, 'sweetness': 1},
         'insight': 'Плотный вкус с горчинкой — как у полнотелых вин с выдержкой: '
                    'насыщенных, долгих, с характером.'},
        {'id': 'milk', 'label': 'Молочный', 'icon': 'choc-milk', 'taste': {'body': 3, 'sweetness': 2},
         'insight': 'Баланс сладости и глубины. Вам подойдут округлые вина средней насыщенности '
                    'со спелым фруктом.'},
        {'id': 'white', 'label': 'Белый', 'icon': 'choc-white', 'taste': {'body': 2.5, 'tannin': 1, 'sweetness': 3},
         'insight': 'Никакой горечи, только сливочная мягкость. Это то же шелковистое ощущение, '
                    'что у вин почти без танинов.'},
    ]},
    {'id': 'juice', 'title': 'Какой сок вы нальёте себе утром?', 'options': [
        {'id': 'grapefruit', 'label': 'Грейпфрутовый или лимонад', 'icon': 'juice-citrus', 'taste': {'acidity': 4.5},
         'insight': 'Вы любите кислинку. В вине это свежесть и «хруст» — бодрые белые, '
                    'игристые и лёгкие красные с живой кислотностью.'},
        {'id': 'apple', 'label': 'Яблочный', 'icon': 'juice-apple', 'taste': {'acidity': 3.5},
         'insight': 'Кислотность в меру — как в спелом яблоке. Это золотая середина: '
                    'вино освежает, но не щиплет.'},
        {'id': 'peach', 'label': 'Персиковый или виноградный', 'icon': 'juice-peach',
         'taste': {'acidity': 2, 'sweetness': 3},
         'insight': 'Вам ближе мягкий, сочный вкус без резкости. Поищем вина с низкой '
                    'кислотностью и спелым фруктом.'},
    ]},
    {'id': 'sugar', 'title': 'Сколько сахара вы кладёте в чай?', 'options': [
        {'id': 'none', 'label': 'Без сахара', 'icon': 'tea-none', 'taste': {'sweetness': 1}, 'sweetness': ['сухое'],
         'insight': 'Вам нравится чистый вкус. Сухие вина почти без сахара: '
                    'в них звучат фрукт, кислотность и выдержка.'},
        {'id': 'one', 'label': 'Ложку', 'icon': 'tea-one', 'taste': {'sweetness': 2},
         'sweetness': ['сухое', 'полусухое', 'полусладкое'],
         'insight': 'Лёгкая сладость вам приятна. Покажем сухие и полусухие вина — '
                    'а иногда и полусладкое к десерту.'},
        {'id': 'more', 'label': 'Две и больше', 'icon': 'tea-more', 'taste': {'sweetness': 4},
         'sweetness': ['полусухое', 'полусладкое', 'сладкое'],
         'insight': 'Вы сладкоежка — и это прекрасно. Полусладкие и десертные вина '
                    'раскрываются ярко и щедро.'},
    ]},
    {'id': 'aroma', 'title': 'Какой аромат вас притягивает?', 'options': [
        {'id': 'berries', 'label': 'Спелые ягоды и вишня', 'icon': 'aroma-berries', 'aromas': ['red_berries', 'dark_berries'],
         'insight': 'Ягодный характер — главное в красных винах: вишня, ежевика, '
                    'чёрная смородина. Будем искать сочные и фруктовые.'},
        {'id': 'citrus', 'label': 'Цитрусы и цветы', 'icon': 'aroma-flowers', 'aromas': ['citrus', 'floral', 'orchard'],
         'insight': 'Цветы, лимонная цедра, белые фрукты — ароматика свежих белых: '
                    'Рислинга, Совиньон Блана, Мускатов.'},
        {'id': 'spice', 'label': 'Ваниль, пряности, дымок', 'icon': 'aroma-spice', 'aromas': ['oak_vanilla', 'spice'],
         'taste': {'oak': 4},
         'insight': 'Это ноты выдержки в дубовой бочке. Вам понравятся вина с бархатной '
                    'глубиной и тёплым послевкусием.'},
        {'id': 'pastry', 'label': 'Свежая выпечка и пузырьки', 'icon': 'aroma-bubbles', 'aromas': ['pastry', 'citrus'],
         'sparkling': True,
         'insight': 'Бриошь и тосты — аромат игристых, выдержанных на осадке. '
                    'Добавим в подборку хорошие брюты.'},
    ]},
]
QUIZ_OPTIONS = {(q['id'], o['id']): o for q in QUIZ for o in q['options']}


def quiz_profile(answers):
    """What the quiz answers describe: averaged axes, aromas, colours, sweetness, sparkling."""
    taste, aromas, colors, sweets, sparkling = {}, [], [], None, False
    # The aroma question speaks for aromas first; the others only add structure.
    for qid, oid in sorted((answers or {}).items(), key=lambda qa: qa[0] != 'aroma'):
        opt = QUIZ_OPTIONS.get((qid, oid))
        if not opt:
            continue
        for axis, value in opt.get('taste', {}).items():
            taste.setdefault(axis, []).append(value)
        aromas.extend(a for a in opt.get('aromas', ()) if a not in aromas)
        colors.extend(c for c in opt.get('colors', ()) if c not in colors)
        if opt.get('sweetness'):
            sweets = list(opt['sweetness'])
        sparkling = sparkling or bool(opt.get('sparkling'))
    if 'tannin' in taste and colors and 'красное' not in colors:
        taste.pop('tannin')  # whites and rosés have almost none: coffee says nothing about them
    return {'taste': {a: round(sum(v) / len(v), 1) for a, v in taste.items()}, 'aromas': aromas[:5],
            'colors': colors, 'sweetness': sweets or [], 'sparkling': sparkling}


def quiz_label(answers):
    """Short words for «Под ваш вкус: …» — colour, body, sweetness, the first aroma."""
    prof = quiz_profile(answers)
    words = []
    if prof['colors'] == ['красное']:
        words.append('красное')
    elif prof['colors']:
        words.append('белое и розовое')
    body = prof['taste'].get('body')
    if body is not None:
        words.append('насыщенное' if body >= 4 else 'лёгкое' if body <= 2.5 else 'среднее по телу')
    sweet = prof['taste'].get('sweetness')
    if sweet is not None:
        words.append('сладкое' if sweet >= 3.5 else 'сухое' if sweet <= 1.5 else '')
    if prof['aromas']:
        words.append(AROMAS[prof['aromas'][0]])
    return ', '.join(w for w in words if w) or 'по анкете'


# Description cue → aroma group (stems, matched as word prefixes).
AROMA_CUES = {  # word prefixes; stems of 3 letters or fewer must match a whole word
    'red_berries': 'вишн череш малин клубн земляни клюкв брусни барбарис красных',
    'dark_berries': 'ежеви черни черной смород черносл слива сливы сливой сливов тутов шелковиц черноплод',
    'citrus': 'цитрус лимон лайм грейп апельс манда помело бергам',
    'orchard': 'яблок груш персик абрик айв нектарин алыч',
    'tropical': 'анана манго маракуй банан личи папай гуав дын',
    'floral': 'цветоч цветков цветов розы розой роза лепест фиалк жасми акаци липов бузин пион лаванд',
    'herbal': 'трав мяты мята эвкал хвои хвоя зелен крапив сено листь томат',
    'spice': 'пряно перец перц кориц гвоздик мускатн аних бадья',
    'oak_vanilla': 'дуб дуба дубов дубе ванил тост копч дыма дымн карам ирис кокос',
    'honey_dried': 'мед меда медов медом сухофр изюм курага инжир финик цукат',
    'mineral': 'минер кремн мелов камен солен морск йод',
    'earthy': 'кожи кожа кожей табак землист гриб лесн подлеск кофе шокол какао',
    'pastry': 'бриош выпеч хлеб дрожж сливочн масл орех миндал фунду',
}
AXIS_CUES = {  # (stem, axis, value)
    'body': [('полнотел', 5), ('насыщ', 4), ('плотн', 4), ('мощн', 5), ('концентр', 4), ('густ', 4),
             ('маслянист', 4), ('округл', 3.5), ('легкое', 2), ('легкий', 2), ('легкая', 2), ('изящн', 2.5),
             ('воздушн', 1.5)],
    'acidity': [('освежающ', 4.5), ('свежая кислот', 4), ('живая кислот', 4.5), ('яркая кислот', 4.5),
                ('высокая кислот', 5), ('хрустящ', 4.5), ('бодрящ', 4.5), ('мягкая кислот', 2.5),
                ('сбалансированная кислот', 3), ('кислотност', 3.5)],
    'tannin': [('мягкие танин', 2.5), ('шелковист', 2.5), ('бархат', 3), ('плотные танин', 4.5),
               ('мощные танин', 5), ('выраженн', 4), ('терпк', 4), ('зрелые танин', 3.5), ('танин', 3.5)],
    'oak': [('дуб', 4), ('дуба', 4), ('дубов', 4), ('дубе', 4), ('бочк', 4), ('баррик', 4), ('выдерж', 3.5), ('ванил', 3.5),
            ('тост', 3.5), ('копч', 3.5), ('нержав', 1), ('амфор', 2.5)],
}

# Typical structure of a variety: (body, acidity, tannin, aroma groups). Expert averages used
# only where the description says nothing; the model's answer always wins.
GRAPES = {
    'каберне совиньон': (4.5, 3.5, 4.5, ('dark_berries', 'oak_vanilla', 'herbal')),
    'мерло': (3.5, 3, 3, ('dark_berries', 'red_berries')),
    'пино нуар': (2.5, 4, 2, ('red_berries', 'earthy')),
    'саперави': (4.5, 4, 4, ('dark_berries', 'spice')),
    'каберне фран': (3.5, 3.5, 3.5, ('red_berries', 'herbal')),
    'сира': (4.5, 3, 4, ('dark_berries', 'spice')), 'шираз': (4.5, 3, 4, ('dark_berries', 'spice')),
    'красностоп золотовский': (4, 4, 4, ('dark_berries', 'spice')), 'красностоп': (4, 4, 4, ('dark_berries', 'spice')),
    'красностоп анапский': (4, 4, 4, ('dark_berries',)),
    'цимлянский черный': (3.5, 3.5, 3, ('dark_berries', 'spice')),
    'мальбек': (4, 3, 4, ('dark_berries',)), 'пти вердо': (4.5, 3.5, 4.5, ('dark_berries', 'floral')),
    'марселан': (4, 3, 3.5, ('dark_berries', 'spice')), 'рубин голодриги': (4, 3.5, 3.5, ('dark_berries',)),
    'бастардо магарачский': (4, 3, 3, ('dark_berries', 'honey_dried')), 'бастардо': (4, 3, 3, ('dark_berries',)),
    'санджовезе': (3.5, 4, 3.5, ('red_berries', 'herbal')), 'темпранильо': (3.5, 3, 3.5, ('red_berries', 'oak_vanilla')),
    'одесский черный': (4, 3.5, 4, ('dark_berries',)), 'первенец магарача': (2.5, 3.5, 1, ('orchard',)),
    'зинфандель': (4, 3, 3, ('dark_berries', 'spice')), 'мурведр': (4, 3, 4, ('dark_berries', 'earthy')),
    'гренаш': (3.5, 2.5, 2.5, ('red_berries', 'spice')), 'неббиоло': (4, 4.5, 5, ('red_berries', 'floral', 'earthy')),
    'карменер': (3.5, 3, 3, ('dark_berries', 'herbal')), 'блауфранкиш': (3.5, 4, 3, ('dark_berries', 'spice')),
    'цвайгельт': (3, 3.5, 2.5, ('red_berries',)), 'пино менье': (2.5, 3.5, 2, ('red_berries',)),
    'менье': (2.5, 3.5, 2, ('red_berries',)), 'пино фран': (2.5, 4, 2, ('red_berries',)),
    'монтепульчано': (4, 3.5, 3.5, ('dark_berries',)), 'голубок': (3.5, 3.5, 3, ('dark_berries',)),
    'августин': (2.5, 3.5, 1, ('orchard',)), 'изабелла': (2.5, 3.5, 2, ('red_berries',)),
    'амурский потапенко': (3.5, 4.5, 3, ('dark_berries',)), 'кефесия': (3.5, 3, 3, ('dark_berries',)),
    'шардоне': (3.5, 3, 1, ('orchard', 'citrus', 'pastry')),
    'рислинг': (2, 5, 1, ('citrus', 'orchard', 'mineral')), 'рислинг рейнский': (2, 5, 1, ('citrus', 'orchard', 'mineral')),
    'совиньон блан': (2.5, 4.5, 1, ('citrus', 'herbal', 'tropical')), 'совиньон': (2.5, 4.5, 1, ('citrus', 'herbal')),
    'совиньон зеленый': (2.5, 4, 1, ('citrus', 'herbal')),
    'ркацители': (2.5, 4.5, 1, ('orchard', 'citrus')), 'алиготе': (2, 4.5, 1, ('citrus', 'orchard')),
    'вионье': (3.5, 2.5, 1, ('orchard', 'floral', 'tropical')),
    'пино блан': (2.5, 3.5, 1, ('orchard', 'floral')), 'пино гри': (2.5, 3, 1, ('orchard',)),
    'пино гриджио': (2, 3.5, 1, ('orchard', 'citrus')),
    'мускат белый': (2.5, 2.5, 1, ('floral', 'honey_dried', 'orchard')), 'мускат': (2.5, 2.5, 1, ('floral', 'orchard')),
    'мускат оттонель': (2.5, 2.5, 1, ('floral', 'orchard')), 'мускат розовый': (2.5, 2.5, 1, ('floral', 'red_berries')),
    'мускат янтарный': (3, 2.5, 1, ('floral', 'honey_dried')), 'мускат гамбургский': (2.5, 2.5, 1, ('floral',)),
    'кокур': (2.5, 4, 1, ('orchard', 'honey_dried')), 'кокур белый': (2.5, 4, 1, ('orchard', 'honey_dried')),
    'цитронный магарача': (2.5, 3, 1, ('citrus', 'floral')), 'бианка': (2.5, 3, 1, ('orchard',)),
    'сибирьковый': (2.5, 4, 1, ('orchard', 'herbal')), 'гевюрцтраминер': (3.5, 2.5, 1, ('floral', 'tropical', 'spice')),
    'траминер': (3.5, 2.5, 1, ('floral', 'spice')), 'траминер розовый': (3.5, 2.5, 1, ('floral', 'spice')),
    'семильон': (3, 3, 1, ('orchard', 'honey_dried')), 'шенен блан': (3, 4.5, 1, ('orchard', 'honey_dried')),
    'сары пандас': (2.5, 3, 1, ('orchard',)), 'цветочный': (2.5, 3, 1, ('floral',)),
    'молдова': (3, 3.5, 2.5, ('dark_berries',)), 'мюллер-тургау': (2, 3, 1, ('orchard', 'floral')),
    'мюллер тургау': (2, 3, 1, ('orchard', 'floral')), 'мцване кахетинский': (3, 3.5, 1, ('orchard', 'floral')),
    'мцване': (3, 3.5, 1, ('orchard', 'floral')), 'верментино': (2.5, 3.5, 1, ('citrus', 'herbal')),
    'сильванер': (2, 3.5, 1, ('orchard', 'herbal')), 'глера': (2, 3.5, 1, ('orchard', 'floral')),
    'альбильо': (2.5, 3, 1, ('orchard',)), 'мальвазия': (3, 2.5, 1, ('floral', 'tropical')),
    'кангун': (2.5, 3.5, 1, ('orchard',)), 'шабаш': (2.5, 3.5, 1, ('orchard', 'citrus')),
    'пухляковский': (2.5, 3, 1, ('orchard',)), 'коломбар': (2, 4, 1, ('citrus', 'tropical')),
    'вердельо': (3, 4, 1, ('citrus',)), 'уньи блан': (2, 4, 1, ('citrus',)), 'алеатико': (3.5, 2.5, 2, ('floral', 'red_berries')),
}
SWEETNESS = (('экстра брют', 1), ('брют', 1), ('полусух', 2), ('полуслад', 3.5), ('сух', 1), ('слад', 5))


def _hit(stem, words, text):
    if ' ' in stem:
        return stem in text
    if len(stem) <= 3:
        return stem in words
    return any(w.startswith(stem) for w in words)


def norm(text):
    return re.sub(r'[^а-яa-z0-9 ]+', ' ', str(text or '').lower().replace('ё', 'е'))


def _color(card):
    text = norm(card.get('category', '') + ' ' + card.get('name', ''))
    return next((c for part in (norm(card.get('category', '')), text) for c in ('красн', 'бел', 'розов', 'оранж') if c in part), '')


def rules_profile(card):
    """Full profile from category, description cues and grape priors."""
    text = norm(card.get('description', ''))
    words = text.split()
    category = norm(card.get('category', '') + ' ' + card.get('name', ''))
    color = _color(card)
    sparkling = 'брют' in category or 'игрист' in category
    grapes = [g.strip().lower() for g in str(card.get('grapes', '')).split(',') if g.strip()]
    known = [GRAPES[g] for g in grapes if g in GRAPES]

    def prior(k):
        vals = [p[k] for p in known]
        return sum(vals) / len(vals) if vals else None

    def cue(axis):
        hits = [v for stem, v in AXIS_CUES[axis] if _hit(stem, words, text)]
        return sum(hits) / len(hits) if hits else None

    prof = {}
    prof['sweetness'] = next((v for root, v in SWEETNESS if root in category), 1.5)
    body = cue('body')
    prof['body'] = body if body is not None else (prior(0) if known else
                                                  4 if color == 'красн' else 2.5)
    acid = cue('acidity')
    prof['acidity'] = acid if acid is not None else (prior(1) if known else 3.5 if color != 'красн' else 3)
    if color in ('бел', 'розов') and not sparkling:
        prof['tannin'] = 1
    elif sparkling:
        prof['tannin'] = 1
    else:
        tannin = cue('tannin')
        prof['tannin'] = tannin if tannin is not None else (prior(2) if known else 3.5)
    if color == 'розов':  # rosé: lighter than the red grapes it is made from
        prof['body'] = min(prof['body'], 2.5)
    if sparkling:
        prof['body'] = min(prof['body'], 3)
        prof['acidity'] = max(prof['acidity'], 4)
    oak = cue('oak')
    prof['oak'] = oak if oak is not None else 1.5
    cues = {g: s.split() for g, s in AROMA_CUES.items()}
    aromas = [g for g, stems in cues.items() if any(_hit(s, words, text) for s in stems)]
    if not aromas:
        for p in known:
            aromas.extend(a for a in p[3] if a not in aromas)
    if sparkling and 'pastry' not in aromas and 'выдерж' in text:
        aromas.append('pastry')
    prof['aromas'] = aromas[:5]
    prof['occasions'] = rules_occasions(prof, sparkling, card)
    prof = {k: (round(v * 2) / 2 if isinstance(v, float) else v) for k, v in prof.items()}
    prof['source'] = 'rules'
    return prof


def rules_occasions(prof, sparkling, card):
    dishes = norm(' '.join(card.get('pairings') or []))
    out = []
    if sparkling:
        out += ['celebration', 'aperitif']
    if prof['sweetness'] >= 3.5 or 'десерт' in dishes:
        out.append('dessert')
    if prof['body'] >= 3.5 or 'мяс' in dishes or 'стейк' in dishes:
        out.append('dinner')
    if prof['body'] <= 2.5 and prof['sweetness'] <= 2 and not sparkling:
        out.append('picnic')
    if prof['oak'] >= 3.5 and prof['body'] >= 4:
        out.append('special')
    return out or ['everyday']


def load_profiles(cards, path=None):
    """slug → profile; model answers override rules field by field."""
    path = Path(path or Path(__file__).resolve().parent / 'content/wine_taste.json')
    model = {}
    if path.exists():
        try:
            model = json.loads(path.read_text()).get('wines', {})
        except (ValueError, OSError):
            model = {}
    out = {}
    for slug, card in cards.items():
        prof = rules_profile(card)
        m = model.get(slug)
        if isinstance(m, dict):
            for k in AXES:
                v = m.get(k)
                if isinstance(v, (int, float)) and 1 <= v <= 5:
                    prof[k] = float(v)
            for k, allowed in (('aromas', AROMAS), ('occasions', OCCASIONS)):
                vals = [x for x in (m.get(k) or []) if x in allowed]
                if vals:
                    prof[k] = vals[:5]
            prof['source'] = 'model'
        out[slug] = prof
    return out
