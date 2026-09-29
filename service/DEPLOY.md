# Сервис распознавания вина: как запустить в проде

Модель: Qwen3-VL-Embedding-8B + LoRA-адаптер v2, поиск по индексу из 2103 вин каталога
(основное фото + доп. фото каталога, схожесть вина = максимум по его фото). Запрос — фото целиком
на 2560 визуальных токенах. Точность на нашем тесте (379 кадров): **93.4% top-1**, 98.9% top-5;
94.2% по уникальным кадрам, если дубли каталога считать одним вином. Подробности —
`qwen_finetune/STATUS_gpu_runs_20260929.md`.

## Что нужно

- GPU с bf16 и ~20 ГБ видеопамяти (модель гоняли на A100 80GB, сервис — на поде RunPod), CUDA 12.x, Python 3.10+,
  torch 2.8 с CUDA (образ RunPod PyTorch 2.8 подходит).
- ~20 ГБ диска: веса модели 17 ГБ качаются с HuggingFace при первом запуске.
- Пакет `wine_prod.tar` (код, адаптер, индекс, карточки вин). Собирается на поде командой
  `bash service/make_bundle.sh`; `WITH_WEIGHTS=1` — положить веса внутрь для сервера без интернета.

## Запуск

```
mkdir -p /opt/wine && tar -xf wine_prod.tar -C /opt/wine && cd /opt/wine
bash service/run_prod.sh
```

Порт 8080 (`PORT=...` меняет). Старт: загрузка модели ~1–2 мин + прогрев; порт открывается,
только когда сервис готов. Один процесс, запросы обрабатываются по одному (GPU).

Переменные окружения:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PORT` | 8080 | порт |
| `NO_WINE_THRESHOLD` | выключен | если задан (рекомендовано 0.60), `/v1/eval/predict` отвечает `{"slug": null}`, когда на фото нет вина |
| `WINE_BACKEND` | `qwen` | `siglip` — старый пайплайн на CPU |
| `HF_HOME` | `./hf` | кеш весов модели |

## API

`GET /health` -> `{"status": "ok", "backend": "qwen", "catalog_size": 2103, "no_wine_threshold": null}`

`POST /v1/eval/predict` (multipart, поле `image`) -> `{"slug": "..."}` — контракт скрипта оценки организаторов.

`POST /v1/search` (multipart, поле `image`) — для интерфейса:

```json
{
  "slug": "abrau-dyurso-...",
  "no_wine": false,
  "card": {"slug": "...", "name": "...", "winery": "...", "region": "...", "grape": "...",
           "category": "...", "color": "...", "description": "...", "score": 0.93},
  "alternatives": [ ...ещё 4 карточки... ],
  "confidence": {"top1_score": 0.93, "gap_top1_top2": 0.05},
  "latency_ms": 700
}
```

`no_wine: true` (схожесть с лучшим вином ниже 0.60) — показать «вино не распознано»; тогда
`slug` и `card` = null, а `alternatives` — 5 ближайших вин. Малый `gap_top1_top2` —
модель сомневается между соседями по линейке, стоит показать альтернативы.

Пример:

```
curl -F image=@photo.jpg http://HOST:8080/v1/eval/predict
curl -F image=@photo.jpg http://HOST:8080/v1/search
```

Латентность на поде от отправки фото до ответа (снимки 4032×3024): среднее 1.2 с, p95 1.5 с.

## Проверка после деплоя

```
python service/check_service.py --url http://HOST:8080/v1/eval/predict --frames data/frames_v2.csv --dups catalog_duplicates.csv
```

(нужны тестовые кадры из пакета пода; ожидается 93.4% без дедупликации).

## Добавить фото к вину без переобучения

Дописать строку `slug,path,kind` в `data/extra_refs.csv`, положить фото в `data/`, на машине с
GPU запустить `python build_index.py` (кодирует только новое) и перезапустить сервис.
