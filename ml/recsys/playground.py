"""Песочница рекомендаций: тот же код, что в приложении, без сервера, сканера и фронта.

    .venv/bin/python ml/recsys/playground.py            # продолжить с прошлого раза
    .venv/bin/python ml/recsys/playground.py --fresh    # начать новичком

Команды (номер вина — из последнего списка):
    найти саперави          поиск по каталогу
    оценить 2 5             оценка 1–5 (0 — снять)
    купить 2 3              положить в коллекцию 3 бутылки
    открыл 2                открыл бутылку (−1)
    нето 2                  «Не то» на рекомендацию
    лента | новое | закончилось
    открыть к рыбе          что открыть из своих бутылок (блюдо необязательно)
    анкета красное сухое мясо rich_oak    цвета, сладость, блюда, плитки — в любом порядке
    паспорт                 вкусовой профиль
    подборка Новый год      создать подборку на 6 и показать подсказки
    чат подбери вино к шашлыку
    я                       что сейчас в коллекции
    выход
"""
import argparse
import shutil
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / 'backend'), str(REPO)]
from bootstrap_dataset import bootstrap  # noqa: E402
from data_store import DataStore  # noqa: E402
from product import Product  # noqa: E402
from taste import TILES  # noqa: E402

HOME = Path.home() / '.wine_recsys_playground'
PROFILE = 'playground'
last = []


def show(items, empty=None):
    global last
    last = [w['slug'] for w in items]
    if not items:
        print('  (пусто)' + (f' — {empty}' if empty else ''))
    for k, w in enumerate(items, 1):
        extra = f' · у вас {w["quantity"]} бут.' if w.get('owned') else ''
        print(f'{k:2d}. {w["name"][:52]}  [{w["category"]}, {w["region"]}]{extra}')
        if w.get('reason'):
            print(f'      ↳ {w["reason"]}')


def pick(arg):
    k = int(arg) - 1
    if not 0 <= k < len(last):
        raise ValueError('нет вина с таким номером в последнем списке')
    return last[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fresh', action='store_true')
    args = ap.parse_args()
    if args.fresh and HOME.exists():
        shutil.rmtree(HOME)
    if not (HOME / 'collector/state.sqlite3').exists():
        bootstrap(HOME, REPO / 'seed/catalog.csv')
    p = Product(DataStore(HOME))
    r = p.recs
    print(f'Каталог: {len(p.cards)} вин. Профиль хранится в {HOME}. «помощь» — список команд.\n')
    show(r.endpoint(PROFILE, 'for_you', '', 8, log=False)['items'])
    while True:
        try:
            line = input('\n> ').strip()
        except EOFError:
            break
        cmd, _, rest = line.partition(' ')
        cmd = cmd.lower()
        try:
            if cmd in ('выход', 'exit', 'q'):
                break
            elif cmd == 'помощь':
                print(__doc__)
            elif cmd == 'найти':
                show(p.search_catalog(rest, PROFILE, 10))
            elif cmd == 'оценить':
                n, v = rest.split()
                p.update(PROFILE, pick(n), {'rating': int(v)})
                print('  оценка сохранена')
            elif cmd == 'купить':
                n, *q = rest.split()
                p.collection.stock(PROFILE, pick(n), {'kind': 'add', 'delta': int(q[0]) if q else 1,
                                                      'request_id': uuid.uuid4().hex})
                print('  добавлено в коллекцию')
            elif cmd == 'открыл':
                p.collection.stock(PROFILE, pick(rest), {'kind': 'open', 'delta': -1, 'request_id': uuid.uuid4().hex})
                print('  минус бутылка')
            elif cmd == 'нето':
                r.feedback(PROFILE, {'slug': pick(rest), 'action': 'not_for_me'})
                print('  учтено: похожие опустятся')
            elif cmd in ('лента', 'новое', 'закончилось', 'открыть'):
                mode = {'лента': 'for_you', 'новое': 'new', 'закончилось': 'refill', 'открыть': 'cellar'}[cmd]
                res = r.endpoint(PROFILE, mode, rest, 8, log=False)
                show(res['items'], res['empty'])
            elif cmd == 'анкета':
                words = rest.lower().replace(',', ' ').split()
                body = {'colors': [w for w in words if w in ('красное', 'белое', 'розовое', 'оранжевое')],
                        'sweetness': [w for w in words if w in ('сухое', 'полусухое', 'полусладкое', 'сладкое')],
                        'tiles': [w for w in words if w in TILES],
                        'dishes': [d for d in r.dishes if any(w in d.lower() for w in words if len(w) > 3)][:6]}
                print('  ответы:', body, '\n  плитки:', ', '.join(f'{k} ({v["label"]})' for k, v in TILES.items()))
                show(r.save_onboarding(PROFILE, body)['recommendations']['items'])
            elif cmd == 'паспорт':
                t = r.passport(PROFILE)
                print(f'  стадия: {t["stage"]}, свидетельств: {t["evidence"]}\n  {t["summary"] or "пока пусто"}')
                for a in t['axes']:
                    print(f'  {a["name"]:16s} {"█" * round(a["value"] * 2):10s} {a["value"]}')
                for key in ('aromas', 'styles', 'grapes', 'regions'):
                    if t[key]:
                        print(f'  {key}: ' + ', '.join(f'{x["name"]} {round(x["share"] * 100)}%' for x in t[key]))
            elif cmd == 'подборка':
                lid = p.collection.save_list(PROFILE, {'name': rest or 'Подборка', 'goal': '', 'size': 6})['id']
                res = r.list_suggestions(PROFILE, lid)
                print(f'  повод: {res["occasion"] or "не распознан"}, мест: {res["free"]}')
                show(res['items'], res['empty'])
            elif cmd == 'чат':
                ans = p.assistant(PROFILE, rest, intent=None)
                print('  ' + ans.get('text', ''))
                show(ans.get('items', []))
            elif cmd == 'я':
                snap = p.collection.snapshot(PROFILE)
                show(snap['items'])
            elif cmd:
                print('  не понял; «помощь» — список команд')
        except Exception as e:  # noqa: BLE001 — песочница не должна падать от опечатки
            print('  ошибка:', getattr(e, 'detail', None) or e)


if __name__ == '__main__':
    main()
