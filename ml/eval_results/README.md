# Результаты скрипта оценки организаторов

Прогон `participant_test.sh` организаторов на их `eval-dataset` против сервиса, развёрнутого из этого
репозитория с чистого клона (`bash setup.sh && bash start.sh`, RunPod, A100 80GB).

```bash
./participant_test.sh --images-dir ./queries --manifest ./queries.tsv \
  --endpoint 'http://127.0.0.1:8080/v1/eval/predict' --output ./predictions.jsonl
```

[`predictions.jsonl`](predictions.jsonl) — по строке на фото: `query_id`, `image_path`, `image_sha256`,
`predicted_slug`, `latency_ms`.

| Фото | Ответ сервиса | Время |
|---|---|---:|
| 019c68d0.jpg | usadba-mezyb-shishka-pino-nuar-rozovoe-suhoe-115 | 1164 мс |
| 02eef911.webp | massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16 | 1079 мс |
| 096ca74e.jpg | kuban-vino-aristov-kyuve-aleksandr-millezimato-shardone-beloe-ekstra-bryut-12 | 1090 мс |

Все ответы — валидные slug каталога (`null` нет), время ответа ~1.1 с. Правильные ответы и итоговую
оценку считают организаторы.
