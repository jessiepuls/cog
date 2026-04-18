from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class Item:
    tracker_id: str  # e.g. "github/<owner>/<repo>"
    item_id: str
    title: str
    body: str
    labels: tuple[str, ...]
    comments: tuple[Comment, ...]
    created_at: datetime
    updated_at: datetime
    url: str
