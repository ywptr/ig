from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI

from app.api.images import router as image_router


app = FastAPI(
    title="IG",
    version="0.1.0",
)


app.include_router(
    image_router,
    prefix="/v1"
)


@app.get("/health")
def health():
    return {
        "status": "ok"
    }
