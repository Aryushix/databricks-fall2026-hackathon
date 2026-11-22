
"""
state_manager.py

Care Finder — Application Orchestrator

Manages:
- Conversation sessions
- Search context
- Pending confirmations
- Confirmed service and location
- Search preferences
- Previous facility recommendations
- Representative assistance sessions
- Search reset and session deletion

Uses Python standard library only.
"""

from copy import deepcopy
from threading import RLock
from typing import Optional
from uuid import uuid4

from schemas import (
    Intent,
    SearchConfirmation,
    SessionState,
    UserRequest,
)


class StateManager:
    """
    In-memory conversation state manager.

    Each conversation is identified by session_id.

    State is copied before being returned so
    external components cannot accidentally
    modify stored conversation information.
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._lock = RLock()

    # ==========================================
    # 1. CREATE OR RETRIEVE SESSION
    # ==========================================

    def get_or_create(
        self,
        session_id: str
    ) -> SessionState:

        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty")

        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState(
                    session_id=session_id
                )

            return deepcopy(self._sessions[session_id])

    # ==========================================
    # 2. GET EXISTING SESSION
    # ==========================================

    def get(
        self,
        session_id: str
    ) -> Optional[SessionState]:

        with self._lock:
            state = self._sessions.get(session_id)

            if state is None:
                return None

            return deepcopy(state)

    # ==========================================
    # 3. UPDATE SESSION FROM USER REQUEST
    # ==========================================

    def update_from_request(
        self,
        request: UserRequest
    ) -> SessionState:
        """
        Main state update method.

        Responsibilities:
        - Process search resets
        - Process confirmation responses
        - Update service and location
        - Preserve unchanged information
        - Invalidate outdated confirmations
        - Create new pending confirmations
        - Update preferences
        """

        with self._lock:

            self.get_or_create(request.session_id)

            state = self._sessions[request.session_id]

            # ----------------------------------
            # RULE 1: RESET SEARCH
            # ----------------------------------

            if request.intent == Intent.RESET_SEARCH:

                self._clear_search(state)

                return deepcopy(state)

            # ----------------------------------
            # RULE 2: HANDLE CONFIRMATION
            # ----------------------------------

            if request.intent == Intent.CONFIRM_SEARCH:

                return self._handle_confirmation(
                    state,
                    request
                )

            # Assistance and unknown intents
            # should not modify search criteria.
            if request.intent not in (
                Intent.FIND_HOSPITAL,
                Intent.REFINE_SEARCH
            ):
                return deepcopy(state)

            criteria_changed = False
            preferences_changed = False

            # ----------------------------------
            # RULE 3: UPDATE SERVICE
            # ----------------------------------

            if request.clear_service:

                if state.service is not None:
                    criteria_changed = True

                state.service = None

            elif (
                request.service is not None
                and not request.service_ambiguous
            ):

                new_service = request.service.strip()

                if new_service and state.service != new_service:

                    state.service = new_service

                    criteria_changed = True

            # ----------------------------------
            # RULE 4: UPDATE LOCATION
            # ----------------------------------

            if request.clear_location:

                if state.location is not None:
                    criteria_changed = True

                state.location = None

            elif (
                request.location is not None
                and not request.location_ambiguous
            ):

                new_location = request.location.strip()

                if new_location and state.location != new_location:

                    state.location = new_location

                    criteria_changed = True

            # ----------------------------------
            # RULE 5: UPDATE PREFERENCES
            # ----------------------------------

            for key, value in request.preferences.items():

                if value is None:

                    if key in state.preferences:
                        del state.preferences[key]
                        preferences_changed = True

                elif state.preferences.get(key) != value:

                    state.preferences[key] = deepcopy(value)

                    preferences_changed = True

            # ----------------------------------
            # RULE 6: INVALIDATE OLD CONFIRMATION
            # ----------------------------------

            has_ambiguity = (
                request.service_ambiguous
                or request.location_ambiguous
            )

            if criteria_changed or has_ambiguity:

                state.pending_confirmation = None

                state.confirmed_service = None
                state.confirmed_location = None

            # ----------------------------------
            # RULE 7: CLEAR OUTDATED RESULTS
            # ----------------------------------

            if criteria_changed or preferences_changed or has_ambiguity:

                state.last_facility_ids = []

            # ----------------------------------
            # RULE 8: CREATE PENDING CONFIRMATION
            # ----------------------------------

            if (
                not has_ambiguity
                and state.service
                and state.location
            ):
                already_confirmed = (
                state.service == state.confirmed_service
                and state.location == state.confirmed_location
                )
                if already_confirmed:

                    state.pending_confirmation = None
                elif (
                    state.pending_confirmation is None
                    or state.pending_confirmation.service != state.service
                    or state.pending_confirmation.location != state.location
                ):
                    state.pending_confirmation = SearchConfirmation(
                    service=state.service,
                    location=state.location,
                    confirmation_id=uuid4().hex
                )

            return deepcopy(state)

    # ==========================================
    # 4. HANDLE USER CONFIRMATION
    # ==========================================

    def _handle_confirmation(
        self,
        state: SessionState,
        request: UserRequest
    ) -> SessionState:
        """
        Accept or reject a specific search proposal.

        A confirmation is valid ONLY when:
        1. A proposal is pending.
        2. The supplied confirmation_id matches.
        3. The proposal matches current search criteria.

        An outdated confirmation never modifies
        the currently pending proposal.
        """
        if (
            request.service is not None
            or request.location is not None
            or request.clear_service
            or request.clear_location
            or request.preferences
        ):
            raise ValueError(
            "Confirmation requests cannot "
            "change search criteria"
        )
        pending = state.pending_confirmation
        # ----------------------------------
        # RULE 1: A PROPOSAL MUST EXIST
        # ----------------------------------

        if pending is None:
            raise ValueError(
                "No pending search confirmation exists"
            )

        # ----------------------------------
        # RULE 2: CONFIRMATION ID MUST MATCH
        # ----------------------------------

        if (
            request.confirmation_id
            != pending.confirmation_id
        ):
            # IMPORTANT:
            # Do not clear the newer pending proposal.
            # A delayed Yes or No must not affect it.

            raise ValueError(
                "Stale or incorrect confirmation_id"
            )

        # ----------------------------------
        # RULE 3: CRITERIA MUST STILL MATCH
        # ----------------------------------

        if (
            state.service != pending.service
            or state.location != pending.location
        ):
            raise ValueError(
                "Search criteria changed "
                "before confirmation"
            )

        # ----------------------------------
        # RULE 4: USER REJECTS PROPOSAL
        # ----------------------------------

        if request.confirmation is False:

            state.pending_confirmation = None

            state.confirmed_service = None
            state.confirmed_location = None

            state.last_facility_ids = []

            # Preserve tentative service/location
            # so the user can correct one field.
            return deepcopy(state)

        # ----------------------------------
        # RULE 5: USER ACCEPTS PROPOSAL
        # ----------------------------------

        if request.confirmation is True:

            state.confirmed_service = pending.service
            state.confirmed_location = pending.location

            state.pending_confirmation = None

            return deepcopy(state)

        raise ValueError(
            "Confirmation must be True or False"
        )


    # ==========================================
    # 5. SAVE RECOMMENDED FACILITIES
    # ==========================================

    def save_facility_ids(
        self,
        session_id: str,
        facility_ids: list[str]
    ) -> SessionState:
        """
        Remember the facilities returned from
        the most recent search.
        """

        with self._lock:

            self.get_or_create(session_id)

            state = self._sessions[session_id]

            state.last_facility_ids = list(facility_ids)

            return deepcopy(state)

    # ==========================================
    # 6. TRACK REPRESENTATIVE ASSISTANCE
    # ==========================================

    def set_assistance_session(
        self,
        session_id: str,
        assistance_session_id: Optional[str]
    ) -> SessionState:
        """
        Associate a representative-assistance
        session with this conversation.

        Passing None clears the association.
        """

        with self._lock:

            self.get_or_create(session_id)

            state = self._sessions[session_id]

            state.assistance_session_id = (
                assistance_session_id
            )

            return deepcopy(state)

    # ==========================================
    # 7. RESET SEARCH
    # ==========================================

    def reset_search(
        self,
        session_id: str
    ) -> SessionState:

        with self._lock:

            self.get_or_create(session_id)

            state = self._sessions[session_id]

            self._clear_search(state)

            return deepcopy(state)

    def _clear_search(
        self,
        state: SessionState
    ) -> None:
        """
        Clear all search-related context.

        Preserve the assistance-session ID.
        """

        state.service = None

        state.location = None

        state.preferences = {}

        state.pending_confirmation = None

        state.confirmed_service = None

        state.confirmed_location = None

        state.last_facility_ids = []

    # ==========================================
    # 8. DELETE SESSION
    # ==========================================

    def delete_session(
        self,
        session_id: str
    ) -> bool:

        with self._lock:

            if session_id in self._sessions:

                del self._sessions[session_id]

                return True

            return False
