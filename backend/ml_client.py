"""Server-only HTTP client for the RunPod wine model."""
import os
from urllib.parse import urlsplit

import requests


class WineMLError(Exception):
    def __init__(self, status_code, message, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


class WineMLClient:
    def __init__(self, url=None, key=None):
        self.url = (url if url is not None else os.environ.get('WINE_ML_URL', '')).rstrip('/')
        self.key = key if key is not None else os.environ.get('WINE_ML_API_KEY', '')
        parsed = urlsplit(self.url)
        loopback = parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port is not None
        if not (parsed.scheme == 'https' and parsed.hostname or loopback) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('WINE_ML_URL must be HTTPS or a loopback HTTP tunnel')
        # An existing Pod proxy may be public. Keep the optional bearer entirely
        # server-side for deployments that protect their inference endpoint.

    def health(self):
        try:
            response = requests.get(self.url + '/health', timeout=(5, 5), allow_redirects=False)
            if response.status_code in (502, 503):
                return {'ready': False, 'catalog_size': 0, 'model_version': ''}
            if 300 <= response.status_code < 400:
                raise ValueError('Unexpected model redirect')
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid model health response')
            # The supplied Pod exposes {status:"ok", backend:"qwen"}, while
            # older deployments expose {ready:true, model_version:"..."}.
            if type(result.get('ready')) is bool:
                ready=result['ready']
            elif result.get('status') in ('ok', 'ready'):
                ready=True
            else:
                raise ValueError('Invalid model health response')
            catalog_size=result.get('catalog_size')
            if type(catalog_size) is not int or catalog_size<0:
                raise ValueError('Invalid model catalog size')
            version=result.get('model_version') or result.get('backend') or ''
            if not isinstance(version,str):
                raise ValueError('Invalid model version')
            return {**result,'ready':ready,'catalog_size':catalog_size,'model_version':version}
        except (requests.RequestException, ValueError) as exc:
            raise WineMLError(503, 'Сервис распознавания временно недоступен. Повторите позже.') from exc

    def search(self, payload, filename='photo.jpg'):
        try:
            headers={'Authorization': 'Bearer ' + self.key} if self.key else {}
            response = requests.post(self.url + '/v1/search',
                headers=headers,
                files={'image': (filename, payload, 'image/jpeg')}, timeout=(5, 80), allow_redirects=False)
            if response.status_code == 400:
                raise WineMLError(422, 'Не удалось прочитать изображение. Выберите другое фото.')
            if response.status_code == 413:
                raise WineMLError(413, 'Файл больше 24 МБ. Выберите другое фото.')
            if response.status_code == 429:
                raise WineMLError(429, 'Сканер занят. Повторите через несколько секунд.', retry_after='2')
            if response.status_code in (401, 403):
                raise WineMLError(503, 'Сервис распознавания временно недоступен. Повторите позже.')
            if response.status_code >= 500:
                raise WineMLError(503, 'Модель недоступна. Повторите через минуту.')
            if 300 <= response.status_code < 400:
                raise WineMLError(503, 'Модель недоступна. Повторите через минуту.')
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid model response')
            return result
        except WineMLError:
            raise
        except requests.Timeout as exc:
            raise WineMLError(504, 'Ответ модели задержался. Повторите сканирование.') from exc
        except (requests.RequestException, ValueError) as exc:
            raise WineMLError(503, 'Нет связи с моделью. Повторите сканирование позже.') from exc
