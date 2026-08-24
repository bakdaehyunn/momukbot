from __future__ import annotations

from dataclasses import dataclass

from momukbot.core.models import RequestLocation


@dataclass(frozen=True)
class PendingNearbySelection:
    location: RequestLocation
    chat_type: str = ""
    first_choice: str = ""


class NearbyState:
    def __init__(self) -> None:
        self.pending_location_text: dict[str, str] = {}
        self.pending_nearby_selection: dict[str, PendingNearbySelection] = {}

    def request_location(self, chat_id: str, text: str) -> None:
        self.pending_nearby_selection.pop(chat_id, None)
        self.pending_location_text[chat_id] = text

    def start_selection(self, chat_id: str, location: RequestLocation, chat_type: str = "") -> None:
        self.pending_location_text.pop(chat_id, None)
        self.pending_nearby_selection[chat_id] = PendingNearbySelection(
            location=location,
            chat_type=chat_type,
        )

    def update_first_choice(self, chat_id: str, selection: PendingNearbySelection, choice: str, chat_type: str) -> None:
        self.pending_nearby_selection[chat_id] = PendingNearbySelection(
            location=selection.location,
            chat_type=chat_type or selection.chat_type,
            first_choice=choice,
        )

    def clear_selection(self, chat_id: str) -> None:
        self.pending_nearby_selection.pop(chat_id, None)
