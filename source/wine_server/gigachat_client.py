"""Server-side GigaChat OAuth and chat transport. Credentials never reach the UI."""
import base64
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


OAUTH_URL = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
CHAT_URL = 'https://api.giga.chat/v1/chat/completions'
FILES_URL = 'https://api.giga.chat/v1/files'
RESPONSE_FORMAT = {
    'type': 'json_schema',
    'schema': {
        'type': 'object',
        'properties': {
            'text': {'type': 'string', 'description': 'Короткий ответ на русском из 1–2 предложений: назови выбранное вино и объясни выбор одним подтверждённым фактом из кандидатов. Не ограничивайся названием вина.'},
            'slugs': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3},
        },
        'required': ['text', 'slugs'],
        'additionalProperties': False,
    },
    'strict': True,
}


class GigaChatClient:
    def __init__(self, authorization_key, client_id, scope, ca_bundle=None):
        try:
            decoded = base64.b64decode(authorization_key, validate=True)
            if not decoded.startswith((client_id + ':').encode('ascii')):
                raise ValueError
        except (ValueError, UnicodeError):
            raise ValueError('GigaChat authorization key and client ID do not match') from None
        if scope != 'GIGACHAT_API_PERS':
            raise ValueError('Unsupported GigaChat scope')
        self.authorization_key = authorization_key
        self.scope = scope
        self.token = None
        self.expires = 0.0
        self.lock = threading.Lock()
        self.ssl_context = ssl.create_default_context()
        if ca_bundle:
            self.ssl_context.load_verify_locations(cafile=ca_bundle)

    def _json_request(self, request, timeout):
        with urllib.request.urlopen(request, timeout=timeout, context=self.ssl_context) as response:
            body = response.read(262145)
        if len(body) > 262144:
            raise ValueError('GigaChat response too large')
        return json.loads(body)

    @staticmethod
    def _timeout(deadline, cap):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError('GigaChat deadline exceeded')
        return min(cap, remaining)

    def _access_token(self, deadline):
        with self.lock:
            if self.token and self.expires > time.monotonic() + 30:
                return self.token
            request = urllib.request.Request(
                OAUTH_URL,
                data=urllib.parse.urlencode({'scope': self.scope}).encode('ascii'),
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                    'RqUID': str(uuid.uuid4()),
                    'Authorization': 'Basic ' + self.authorization_key,
                },
            )
            result = self._json_request(request, self._timeout(deadline, 7))
            token = result.get('access_token')
            if not isinstance(token, str) or not token:
                raise ValueError('Missing GigaChat access token')
            expires_at = result.get('expires_at')
            if expires_at is None:
                lifetime = 25 * 60
            else:
                expiry = float(expires_at)
                if expiry > 10**11:  # GigaChat returns Unix milliseconds.
                    expiry /= 1000
                lifetime = min(25 * 60, expiry - time.time() - 60)
                if lifetime <= 0:
                    raise ValueError('Expired GigaChat access token')
            self.token = token
            self.expires = time.monotonic() + lifetime
            return token

    def _authorized_json(self, url, data, content_type, deadline, cap):
        for attempt in range(2):
            token = self._access_token(deadline)
            request = urllib.request.Request(
                url, data=data,
                headers={'Content-Type': content_type, 'Accept': 'application/json',
                         'Authorization': 'Bearer ' + token},
            )
            try:
                return self._json_request(request, self._timeout(deadline, cap))
            except urllib.error.HTTPError as exc:
                if exc.code != 401 or attempt:
                    raise
                with self.lock:
                    if self.token == token:
                        self.token = None
                        self.expires = 0
        raise RuntimeError('GigaChat authentication failed')

    def _upload_image(self, image_bytes, deadline):
        if not isinstance(image_bytes, bytes) or not image_bytes.startswith(b'\xff\xd8\xff') or len(image_bytes)>15*1024*1024:
            raise ValueError('Invalid GigaChat image')
        boundary='wine-'+uuid.uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\ngeneral\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="scan.jpg"\r\n'
              f'Content-Type: image/jpeg\r\n\r\n').encode()+image_bytes+f'\r\n--{boundary}--\r\n'.encode()
        result=self._authorized_json(FILES_URL,body,'multipart/form-data; boundary='+boundary,deadline,10)
        file_id=result.get('id')
        try:uuid.UUID(file_id)
        except (ValueError,TypeError,AttributeError):raise ValueError('Invalid GigaChat file ID') from None
        return file_id

    def _delete_image(self, file_id):
        try:
            token=self.token
            if not token:return
            request=urllib.request.Request(FILES_URL+'/'+file_id+'/delete',data=b'',
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
            with urllib.request.urlopen(request,timeout=2,context=self.ssl_context):pass
        except Exception:
            # Do not log the provider's response: it might contain credentials.
            logging.getLogger(__name__).warning('GigaChat attachment cleanup failed')

    def complete(self, payload, image_bytes=None):
        deadline = time.monotonic() + 25
        file_id=None
        try:
            if image_bytes:
                file_id=self._upload_image(image_bytes,deadline)
                messages=[dict(message) for message in payload['messages']]
                messages[-1]['attachments']=[file_id]
                payload={**payload,'messages':messages}
            requested_format = payload.get('response_format')
            response_format = requested_format if isinstance(requested_format, dict) and requested_format.get('type') == 'json_schema' else RESPONSE_FORMAT
            data = json.dumps({**payload, 'response_format': response_format}, ensure_ascii=False).encode('utf-8')
            return self._authorized_json(CHAT_URL,data,'application/json',deadline,18)
        finally:
            if file_id:self._delete_image(file_id)
