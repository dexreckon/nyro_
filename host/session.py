"""Session hygiene: reset the --resume id every N turns or when the persona changes."""
from __future__ import annotations


class SessionManager:
    def __init__(self, reset_every: int = 5):
        self.reset_every = reset_every
        self.persona: str | None = None
        self.session_id: str | None = None
        self.turns = 0

    def resume_id(self, persona: str) -> str | None:
        """Session id to resume for this persona, or None for a fresh session."""
        if persona != self.persona or self.turns >= self.reset_every:
            self.reset()
            self.persona = persona
            return None
        return self.session_id

    def record(self, persona: str, session_id: str | None) -> None:
        self.persona = persona
        if session_id:
            self.session_id = session_id
        self.turns += 1

    def reset(self) -> None:
        self.persona, self.session_id, self.turns = None, None, 0
