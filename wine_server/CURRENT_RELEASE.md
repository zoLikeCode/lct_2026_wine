# Сайт «Своё Вино» — текущая версия

Снимок кода, опубликованного 17.10.2026. PWA-кеш: `wine-server-v31-collection-account`.

## Что находится в этой папке

- `app.py`: FastAPI, маршруты сканера и админки; `ml_client.py` — подключение внешнего ML API.
- `product.py`: карточки, предпочтения, история и рекомендации.
- `collection.py`: коллекции, количество бутылок, журнал операций, аккаунты и сессии.
- `static/mobile`: пользовательский интерфейс и публичные ресурсы карточек портала.
- `static/admin`: разметка снимков, границ этикеток, дубли и выгрузки.
- `content/portal_wines.json`: публичные сведения портала; это не история пользователей.
- `tests`: проверки на временных синтетических данных, без рабочих аккаунтов.
- `../wine_curation`: общий слой хранения и операций с фотографиями.

## Запуск

Python 3.12. Команды ниже выполняются из корня репозитория.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r wine_server/requirements.txt
export WINE_DATASET=/absolute/path/to/wine_dataset
export WINE_ADMIN_PASSWORD='replace-with-your-own-long-password'
export WINE_INFERENCE_BACKEND=local
python wine_server/app.py
```

Приложение слушает `127.0.0.1:8100`. Для production нужен HTTPS-прокси;
пример systemd и nginx — в `deploy/`. Для HTTP только на локальном компьютере
нужно установить `WINE_COOKIE_SECURE=0`; в production оставить защищённые cookie.

`WINE_DATASET` должен указывать на существующий набор с `catalog.csv` и
`collector/state.sqlite3`. Фотографии и данные пользователей распространяются отдельно.
Чтобы создать **новую пустую** базу, имея внешний CSV каталога:

```bash
python wine_server/deploy/portable/bootstrap_dataset.py \
  --dataset /absolute/path/to/wine_dataset \
  --catalog /absolute/path/to/catalog.csv
```

Для локального инференса положите отдельный совместимый комплект весов и индекса
в `wine_server/models/`: `provenance.json`, `index.json`, `index.npz`,
`geometry.npz`, `encoder.onnx`, `yolo.onnx` и модели OCR. Состав и контрольные
суммы проверяет `inference.py`. Без комплекта локальная модель не готова к сканированию.
Альтернатива: `WINE_INFERENCE_BACKEND=runpod`, `WINE_ML_URL=https://your-ml-host`,
при необходимости `WINE_ML_API_KEY`. Внешний сервис должен поддерживать контракт
`POST /v1/search` из `ml_client.py`; веса остаются на его стороне.

GigaChat подключается только через переменные окружения, перечисленные
в `deploy/portable/.env.example`. Без ключей работает подбор по каталогу.
Никакие ключи, пароли, `.env`, базы, пользовательские фотографии или веса в Git не включены.

## Проверка без моделей и личных данных

```bash
pip install -r wine_server/requirements-dev.txt
python -m unittest discover -s wine_server/tests -p 'test_collection.py'
python -m unittest discover -s wine_server/tests -p 'test_product.py'
python -m unittest discover -s wine_server/tests -p 'test_runpod_integration.py'
```

Тесты создают временные базы и используют подменный инференс.
Они проверяют API и сохранение данных, а не точность распознавания.

## Обновление существующего сервера

Перед обновлением сделайте согласованную резервную копию SQLite через
`scripts/backup_metadata.py` и сохраните предыдущий код. `deploy/publish.sh`
переносит код, статические ресурсы и публичный контент; не переносит базу,
модели и пользовательские фото. По умолчанию адресует текущий VPS;
переопределение — `WINE_SSH_TARGET`. Перезапуск временно прерывает доступ
к инференсу, пока модель загружается. Для отката приложения возвращайте код,
не перезаписывая рабочую базу старой копией.

Коллекция расширяет существующую схему SQLite при запуске. Прежние оценки
сохраняются; прежнее избранное отображается как «Хочу попробовать».
Регистрация использует логин и пароль, восстановление — одноразовый
резервный код. Почтовая авторизация и уведомления не подключены.

`README.md` в этой папке содержит историческую документацию этапов проекта;
указанные там объёмы датасета и замеры относятся к датам тех этапов.
Папка `deploy/portable` — отдельный сборщик ZIP, которому дополнительно
нужен исходный `wine_dataset` рядом с `wine_server`; сам датасет в Git отсутствует.
