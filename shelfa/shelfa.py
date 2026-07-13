from fastapi import FastAPI
import shelfa.config as config
import shelfa.database as database
import shelfa.services.notifications as notifications
import shelfa.routers.fcm as fcm
import shelfa.routers.identity as identity
import shelfa.routers.messages as messages
import shelfa.routers.files as files
    
import logging

logger = logging.getLogger("xvsx")

def init_shelfa(app: FastAPI) -> None:
    logger.info("Creating directories...")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    config.PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Directories created.")

    database.init_db()
    logger.info("Database initialized.")

    notifications.init_firebase()
    logger.info("Firebase initialized.")

    app.include_router(fcm.router)
    logger.info("FCM router added.")

    app.include_router(identity.router)
    logger.info("Identity router added.")

    app.include_router(messages.router)
    logger.info("Messages router added.")

    app.include_router(files.router)
    logger.info("Files router added.")
