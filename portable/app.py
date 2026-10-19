"""Standalone API; no admin credentials, dataset, photos or research environment."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, HTTPException, UploadFile
from .images import MAX_BYTES, ImageTooLarge, load_image


def create_app(model_dir=None, engine=None):
    @asynccontextmanager
    async def lifespan(app):
        if engine is None:
            from .inference import Engine
            app.state.engine = await asyncio.to_thread(Engine, model_dir)
        else:
            app.state.engine = engine
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(title='Wine scanner — portable ONNX', version='1.0', lifespan=lifespan)
    app.state.ready = False
    gate = asyncio.Semaphore(1)

    @app.get('/health')
    def health():
        if not app.state.ready:
            raise HTTPException(503, 'Model not ready')
        return {'ready': True, 'model_version': app.state.engine.version,
                'catalog_count': len(app.state.engine.slugs)}

    async def run(image):
        try:
            payload = await image.read(MAX_BYTES + 1)
        finally:
            await image.close()
        async with gate:
            try:
                picture = await asyncio.to_thread(load_image, payload)
            except ImageTooLarge as exc:
                raise HTTPException(413, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return await asyncio.to_thread(app.state.engine.predict, picture)

    @app.post('/v1/eval/predict')
    async def eval_predict(image: UploadFile = File(...)):
        result = await run(image)
        return {'slug': result['slug']}

    @app.post('/v1/predict')
    async def predict(image: UploadFile = File(...)):
        result = await run(image)
        result['portal_url'] = 'https://vino-svoe.ru/wines/' + result['slug']
        result['wine'] = app.state.engine.catalog.get(result['slug'], {})
        return result

    return app


app = create_app()
