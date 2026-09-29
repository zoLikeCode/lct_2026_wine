#!/usr/bin/env python3
"""Regenerate the human-readable dataset report from the current manifest/state."""
import collections,json
from pathlib import Path
import collect as c

c.init(c.DEFAULT_CSV);stats=c.export()
rows=[dict(r) for r in c.DB.execute('SELECT * FROM images')]
counts=collections.defaultdict(collections.Counter)
for r in rows:
    group='needs_review' if r['status']=='needs_review' else 'accepted'
    counts[r['source']][group]+=1
sources=[]
for source,ct in sorted(counts.items()):
    products=c.DB.execute('SELECT count(*) FROM products WHERE source=?',(source,)).fetchone()[0]
    if source=='vino-svoe.ru':products=c.DB.execute('SELECT count(*) FROM profiles').fetchone()[0]
    sources.append({'source':source,'catalog_records_examined':products,**{k:ct[k] for k in ('accepted','needs_review')}})
c.write_csv(c.ROOT/'sources.csv',sources,['source','catalog_records_examined','accepted','needs_review'])
checks=json.loads((c.ROOT/'collector'/'visual_checks.json').read_text()) if (c.ROOT/'collector'/'visual_checks.json').exists() else []
current_ids={r['id'] for r in rows}
checks=[r for r in checks if r['id'] in current_ids]
front_checks=list({v['sha256']:v for v in c.FRONT_DECISIONS.values()}.values())
exclusions=[json.loads(line) for line in (c.ROOT/'excluded_non_front.jsonl').read_text().splitlines()] if (c.ROOT/'excluded_non_front.jsonl').exists() else []
stats['unique_file_hashes']=len({r['sha256'] for r in rows})
stats['unique_pixel_hashes']=len({r['pixel_sha256'] for r in rows})
stats['source_domains']=len(counts)
stats['manual_visual_sample_size']=len(checks)
stats['accepted_reference_images']=counts['vino-svoe.ru']['accepted']
stats['front_view_visual_checks']=len(front_checks)
stats['excluded_non_front_files']=len({r['id'] for r in exclusions})
(c.ROOT/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
verification=json.loads((c.ROOT/'verification.json').read_text()) if (c.ROOT/'verification.json').exists() else {}
resume=json.loads((c.ROOT/'resume_verification.json').read_text()) if (c.ROOT/'resume_verification.json').exists() else {}
enrichment=json.loads((c.ROOT/'enrichment_summary.json').read_text()) if (c.ROOT/'enrichment_summary.json').exists() else {}
extension=''
if enrichment:
    extension=f"""## Дополнение по эталонной картинке

После перехода к сопоставлению с изображениями карточек `vino-svoe.ru/wines/<slug>` принято изображений: **{enrichment['before']['accepted']:,} → {stats['accepted']:,}**, чистый прирост **{enrichment['net_accepted_gain']:,}**.

- Новые загрузки, вошедшие в `images/`: {enrichment['new_accepted_downloads']:,}.
- Ранее спорные кандидаты, подтверждённые сравнением с эталоном: {enrichment['previous_candidates_promoted']:,}.
- Позиций с добавленными снимками: {enrichment['slugs_with_additions']:,}.
- Рассмотрено ссылок на кандидаты: {enrichment['candidate_urls_examined']:,}; пригодно для сравнения: {enrichment['usable_candidate_urls']:,}.
- Дополнительно визуально просмотрено пар «эталон — кандидат»: {enrichment['manual_reference_pair_checks']:,}.

Использованы признаки деталей этикетки и геометрическая проверка совпадений, затем название, серия, категория, сахар и год. Число совпавших деталей не является вероятностью правильного ответа. Текст читается локально для проверки мелких различий; одинаковый дизайн сам по себе не подтверждает год. Общие эталоны нескольких slug и обнаруженные ошибочные картинки не разрешаются автоматически.

Новые принятые файлы перечислены в `new_images.csv`; основания сравнения — в `reference_matches.csv`; итог до/после — в `enrichment_summary.json`. Новые неподтверждённые поисковые кандидаты оставлены в рабочем кэше вне папок для обучения. Ранее созданная папка `needs_review/` сохранена.

"""
attempts=collections.Counter(r[0] for r in c.DB.execute('SELECT status FROM attempts'))
coverage=stats['coverage']
source_table='\n'.join(f"| {s['source']} | {s['catalog_records_examined']} | {s['accepted']} | {s['needs_review']} |" for s in sources)
readme=f'''# Интернет-датасет российских вин — лицевая сторона

Собран {stats['updated_at']}. Каталог: **2 103 уникальных slug** из предоставленного CSV (4 147 строк до удаления повторов). Все 2 103 папки `images/<slug>/` созданы.

**Набор пересобран для распознавания лицевой этикетки.** Сохраняются бутылки, снятые спереди или под углом с видимой лицевой этикеткой, и крупные планы лицевой этикетки. Контрэтикетки с составом и служебным текстом, пробки, отдельная подарочная упаковка и инфографика исключены из всех папок с изображениями, включая `needs_review/`. Папка `auxiliary/` больше не используется.

## Фактический результат

- Принято в `images/`: **{stats['accepted']:,}** изображений.
- Из них эталоны со «Своего Вина»: **{stats['accepted_reference_images']:,}**. Они не считаются дополнительным разнообразием.
- Дополнительные принятые изображения из других источников: **{stats['additional_internet']:,}**.
- Кандидаты с лицевой стороной в `needs_review/`: **{stats['needs_review']:,}**. Их точное соответствие slug/году не подтверждено; они **не включены** в число принятых фотографий.
- Из предыдущего набора исключено **{stats['excluded_non_front_files']:,}** файлов с неподходящим видом/содержимым. Причины записаны в `excluded_non_front.jsonl`; самих исключённых фотографий в датасете нет.
- Всего сохранённых файлов: **{stats['downloaded_files']:,}**, уникальных файлов по SHA-256: **{stats['unique_file_hashes']:,}**. Один спорный снимок может находиться в папках нескольких предполагаемых slug; это не несколько разных фотографий.
- Объём самих изображений: **{stats['bytes']/1024**3:.2f} GiB**. Кэш страниц и служебная база занимают дополнительное место.

| Принятых фото на slug | Позиций |
|---|---:|
| 0 | {coverage['0']} |
| 1 | {coverage['1']} |
| 2–4 | {coverage['2-4']} |
| 5 и более | {coverage['5+']} |

**Цель 5–10 различных фотографий на каждую позицию не достигнута.** Пустые папки и недобор оставлены явно. Копии, другие годы и похожие вина не добавлялись ради количества. Многие доступные интернет-изображения остаются студийными; крупные этикетки и кадры в реальной обстановке чаще встречаются у магазинов. Это набор для обучения/эталонов, а независимую оценку следует проводить на ваших будущих полевых снимках.

{extension}
## Что лежит в папке

```text
images/<slug>/        принятые бутылки и лицевые этикетки
needs_review/<slug>/  лицевая сторона, но неоднозначное соответствие вину/году
catalog.csv          очищенный исходный каталог, все поля сохранены
manifest.csv         источники, прямые ссылки, размеры, хэши и основания сопоставления
coverage.csv         покрытие каждой позиции и причины пробелов
new_images.csv       добавленные и подтверждённые при последнем расширении снимки
reference_matches.csv  сравнение с эталоном и основания принятия/отклонения
reference_issues.csv   обнаруженные неподходящие изображения каталога
recovered_references.csv  эталоны, восстановленные по уникальному имени файла
enrichment_summary.json  фактический результат расширения до/после
sources.csv          сводка по источникам
label_text.csv       результат автоматического чтения этикеток для аудита
year_conflicts.csv   обнаруженные противоречия годов
catalog_ambiguities.csv  группы повторяющихся названий и имён исходных фото
download_attempts.csv   ошибки, отклонённые файлы и исключённые дубли
discarded_duplicates.jsonl  происхождение удалённых копий и ссылка на сохранённый снимок
excluded_non_front.jsonl    журнал исключения задних и вспомогательных видов
verification.json    проверка файлов и контрольных сумм
resume_verification.json  проверка повторного запуска без скачиваний и изменения набора
collector/           возобновляемый Python-сборщик, база прогресса и кэш
```

## Как трактовать качество

`accepted` означает, что снимок прошёл реализованные правила сопоставления. Это **не гарантия безошибочной ручной разметки всего набора**. В новом проходе сопоставляются детали эталонной этикетки, производитель, название/серия, категория, сахар, указанные сорта и год. Предыдущий отбор по описанию также учитывал крепость при наличии структурированного значения. Неподтверждённые признаки и обнаруженные противоречия направляются на проверку; отсутствующие или ошибочные данные источника могут остаться невыявленными.

Для «Своего Вина» основной признак — точная карточка по slug. Все её снимки помечены `catalog_reference`. Для части отсутствующих карточек сопоставлено уникальное имя фото из исходного CSV с файлом предоставленного архива; после визуального просмотра загружен соответствующий открытый ресурс портала. Основание записано в `recovered_references.csv`. Это не означает, что удалённая карточка снова доступна. Обнаруженные ошибочные картинки перечислены в `reference_issues.csv`. Одинаковые пиксели у разных slug требуют проверки; при достаточных визуальных и текстовых основаниях принимается только одна точная позиция.

Текст этикеток дополнительно читался локальным распознавателем macOS. Отдельно проверялись прочитанные годы: противоречащие каталогу/читаемому эталону снимки отправляются в `needs_review`. Год основания производителя, адрес или дата розлива могут давать ложный сигнал, поэтому конфликт OCR — основание для проверки, а не окончательный диагноз. Отсутствие прочитанного года не доказывает совпадение.

Визуально выполнено **{len(checks)}** проверок: случайные выборки по источникам и отдельный просмотр найденных конфликтов явного года; журнал — `collector/visual_checks.json`. Это выборочная проверка дизайна и производителя. Все спорные кандидаты сохранены отдельно; их принадлежность не считается окончательно установленной. Автоматическая проверка не заменяет полного ручного просмотра трудных серий.

При пересборке дополнительно просмотрено **{len(front_checks)} уникальных изображений**: весь ранее неопределённый тип кадра, подозрительные результаты распознавания текста, кадры без распознанного текста и случайная выборка по восьми источникам. Решения сохранены в `collector/front_view_decisions.json`. Проверка ракурса не подтверждает точный slug или год. Остальные кадры отобраны по роли изображения в карточке источника и проверены на признаки контрэтикетки в распознанном тексте. Полного ручного просмотра каждого файла не проводилось.

Фотографии извлекаются из карточки конкретного товара. Блоки рекомендаций не используются. Контрэтикетки и другие неподходящие виды отклоняются до загрузки; неизвестный ракурс допускается только после сохранённой визуальной проверки. Ошибочные подписи источника исправлены адресными решениями по URL. Маленькие изображения (длинная сторона <480 px или короткая <70 px), неоткрывающиеся файлы и неподдерживаемые форматы отклоняются.

Внутри slug точные копии исключаются по SHA-256/пикселям; совпадения между разными slug изолируются. Почти одинаковые версии дополнительно проверяются по изображению после нормализации полей и размера; это эвристика, поэтому абсолютная полнота удаления всех переснятых/ретушированных копий не гарантируется. Кроп лицевой этикетки и фотография бутылки хранятся как разные виды изображения, но не обязательно означают разные фотографические экспозиции.

## Источники

| Источник | Карточек/записей изучено | Принято | На проверку |
|---|---:|---:|---:|
{source_table}

Страницы и прямые ссылки на каждое изображение сохранены в `manifest.csv`. Вино-гид РБК может иметь несколько записей разных лет для одной карточки; идентификатор записи сохранён в служебных метаданных и фрагменте ссылки. WineStyle ограничил массовый обход ответами 403/429, Winemore — 503: обход остановлен, использованы доступные страницы. SimpleWine и Luding вернули 403, Bonvi — проверку доступа; эти источники не добавлены. Ограничения доступа не обходились. Платные API не применялись. Изображения не снабжены единой открытой лицензией; сведения об источнике сохраняются, права остаются у их владельцев.

## Добавление собственных фотографий

Каждый slug — идентификатор точной карточки на портале «Своё Вино»: `https://vino-svoe.ru/wines/<slug>`. Ссылка сохранена в столбце `portal_url` файлов `catalog.csv` и `coverage.csv`. Папка `images/<slug>/` соответствует этой карточке. Ссылки сформированы из slug; добавление столбца не означает повторную проверку доступности всех страниц.

Найдите slug в `catalog.csv` и добавляйте снимки в соответствующую папку `images/<slug>/`, например `own_001.jpg`. Не переносите содержимое `needs_review/` в обучающий набор без проверки. Имена новых файлов не должны совпадать с существующими. Интернет-манифест автоматически не описывает вручную добавленные файлы; для собственной съёмки сохраняйте отдельный журнал сессий/магазинов, чтобы корректно разделить обучение и проверку.

## Продолжение сбора

Нужен Python 3.10+ с зависимостями из `collector/requirements.txt`. Из папки датасета:

```bash
python3 -m pip install -r collector/requirements.txt
python3 collector/collect.py retail
python3 collector/reference_search.py refs
python3 collector/reference_search.py pool
python3 collector/reference_search.py match
python3 collector/reference_search.py ocr-input
```

Затем выполните локальное распознавание подготовленного `ocr_input.json` утилитой из `collector/ocr.swift` (macOS, Foundation/Vision). Каждый дополнительный запуск записывайте в отдельный `new_ocr*.jsonl`, чтобы сохранить предыдущие результаты. Путь рабочего кэша вычисляется сборщиком относительно папки проекта: `work/wine_enrichment/`. После распознавания:

```bash
python3 collector/reference_search.py decide
python3 collector/reference_search.py sheets --limit 144
```

Просмотрите пары на листах и сохраните спорные решения в `collector/reference_visual_overrides.json`. Галерейные кадры с неизвестным видом дополнительно требуют записи визуального решения по URL в `collector/front_view_decisions.json`; одного совпадения рисунка этикетки недостаточно для допуска такого кадра. Текущие сохранённые решения повторно применяются автоматически. После просмотра повторите `decide`, затем:

```bash
python3 collector/reference_search.py apply
python3 collector/enrichment_report.py ocr
python3 collector/enrichment_report.py audit
python3 collector/audit.py dedupe
python3 collector/collect.py verify
python3 collector/enrichment_report.py report
python3 collector/report.py
```

Первый вызов продолжает загрузку уже найденных карточек. Сохранённые снимки и зафиксированные дубли повторно не скачиваются. Временные ошибки повторяются до трёх раз; ошибки загрузки можно повторить следующим запуском. Ограничение — не более двух начал HTTP-запросов в секунду на домен в одном процессе; не запускайте два сборщика одного источника одновременно.

Для обновления источников используйте отдельно `collect.py svoe`, `collect.py cigar-crawl`, `expand.py --url https://www.cru.ru/wine/russia/`, `expand.py --url https://www.cru.ru/champagne-and-sparkling/russia/`, `expand.py --url https://wine.rbc.ru/vino/`, `official.py fanagoria` и `official.py vibes`. Сначала обновите карточки, затем запустите `collect.py retail`. Новые данные требуют повторного аудита; текущие результаты ручной выборки не распространяются на будущие загрузки.

Кэш и `collector/state.sqlite3` нужны для возобновления. Персональные аккаунты и секретные ключи сборщику не требуются.

## Проверка поставки

Проверено файлов: **{verification.get('checked_files','ещё не выполнено')}**. Ошибок: **{len(verification.get('errors',[]))}**. Каталожных папок: **{verification.get('image_directories',2103)}**. Записей исключённых дублей в журнале загрузок: **{attempts['duplicate']}**. Итоговый машиночитаемый отчёт: `summary.json`.

Проверка повторного запуска: состав изображений {'не изменился' if resume.get('unchanged_images') else 'не подтверждён'}, обращений за повторной загрузкой: {resume.get('network_requests','не проверено')}. Проверялись идентификаторы, slug, пути, статусы и SHA-256 всех зарегистрированных файлов.
'''
(c.ROOT/'README.md').write_text(readme,encoding='utf-8')
c.log('report_written',**stats)
