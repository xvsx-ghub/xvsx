from fastapi import FastAPI

app = FastAPI(title="Server Template", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "Hello from FastAPI"}
