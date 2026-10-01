from fastapi import FastAPI

from app.api import dead_letter, devices, mocks, monitoring, notifications, preferences, templates
from app.core.errors import register_exception_handlers

app = FastAPI(title="Alert System")
register_exception_handlers(app)

app.include_router(templates.router)
app.include_router(devices.router)
app.include_router(preferences.router)
app.include_router(monitoring.router)
app.include_router(dead_letter.router)
app.include_router(mocks.router)
app.include_router(notifications.router)
