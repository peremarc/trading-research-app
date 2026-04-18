from app.core.config import get_settings
from app.domains.system.services import SchedulerService

settings = get_settings()
scheduler_service = SchedulerService(settings)
