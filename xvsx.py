import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
import shelfa.shelfa as shelfa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("xvsx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Shelfa...")

    try:
        shelfa.init_shelfa(app)
        logger.info("Shelfa initialized successfully.")
    except Exception:
        logger.exception("Exception during Shelfa initialization")
        raise

    yield

    logger.info("Application shutting down...")


app = FastAPI(
    title="xvsx",
    version="1.0.0",
    lifespan=lifespan,
)

logger.info("Starting the FastAPI application...")


@app.get("/status")
async def status():
    logger.info("Let's rock and roll!")
    return {"message": "Let's rock and roll!"}