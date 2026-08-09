from fastapi import FastAPI
from shelfa_group import api, fcm
import shelfa_group.config as config
import shelfa_group.db as db
    
import logging



logger = logging.getLogger("shelfa")


def init_shelfa(app: FastAPI) -> None:   
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    config.PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Directories created.")

    db.init()
    logger.info("Database initialized.")
    
    fcm.init()
    logger.info("Firebase initialized.")
    
    app.include_router(api.api_router)
    logger.info("API router added.")    
