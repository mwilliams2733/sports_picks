from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.models import ApiUsage

class ApiBudgetTracker:
    def __init__(self, session: Session, monthly_limit: int = 500, pause_at: int = 450):
        self.session = session
        self.monthly_limit = monthly_limit
        self.pause_at = pause_at

    def _current_month(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m")

    def _get_or_create(self, source: str) -> ApiUsage:
        month = self._current_month()
        usage = self.session.query(ApiUsage).filter_by(source=source, month=month).first()
        if not usage:
            usage = ApiUsage(source=source, month=month, request_count=0, updated_at=datetime.now(tz=timezone.utc))
            self.session.add(usage)
            self.session.commit()
        return usage

    def can_make_request(self, source: str) -> bool:
        usage = self._get_or_create(source)
        return usage.request_count < self.pause_at

    def record_request(self, source: str) -> None:
        usage = self._get_or_create(source)
        usage.request_count += 1
        usage.updated_at = datetime.now(tz=timezone.utc)
        self.session.commit()

    def get_count(self, source: str) -> int:
        usage = self._get_or_create(source)
        return usage.request_count
