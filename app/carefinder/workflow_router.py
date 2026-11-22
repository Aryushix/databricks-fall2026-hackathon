
"""
workflow_router.py

Care Finder — Application Orchestrator

Decides the next workflow action based on:
- The interpreted user request
- Current conversation state
- Missing or ambiguous information
- Search confirmation status
- Human assistance requests

The router does NOT execute any action.
It only tells main.py what should happen next.

Uses Python standard library only.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from schemas import (
    Intent,
    SearchConfirmation,
    SearchQuery,
    SessionState,
    UserRequest,
)

# ==========================================
# 1. AVAILABLE WORKFLOW ACTIONS
# ==========================================

class WorkflowAction(str, Enum):

    SEARCH = "search"

    CLARIFY = "clarify"

    # NEW: Request user confirmation.
    CONFIRM = "confirm"

    ASSIST = "assist"

    RESET = "reset"


# ==========================================
# 2. ROUTER DECISION STRUCTURE
# ==========================================

@dataclass
class RouteDecision:
    """
    Describes the action main.py should execute.

    action:
        Which workflow to execute.

    reason:
        Machine-readable reason for the decision.

    missing_fields:
        Information requiring clarification.

    search_query:
        Only provided when SEARCH is selected.

    confirmation:
        Only provided when CONFIRM is selected.
    """

    action: WorkflowAction

    reason: str

    missing_fields: list[str] = field(
        default_factory=list
    )

    search_query: Optional[SearchQuery] = None

    confirmation: Optional[
        SearchConfirmation
    ] = None


# ==========================================
# 3. WORKFLOW ROUTER
# ==========================================

class WorkflowRouter:
    """
    Determines the next action.

    Important:
    The StateManager must update the session
    BEFORE the router processes the request.

    Execution order:

    1. Receive UserRequest
    2. StateManager updates SessionState
    3. WorkflowRouter decides next action
    4. main.py executes the action
    """

    def route(
        self,
        request: UserRequest,
        state: SessionState
    ) -> RouteDecision:

        # ----------------------------------
        # RULE 1: VALIDATE SESSION
        # ----------------------------------

        if request.session_id != state.session_id:

            raise ValueError(
                "Request and state session IDs "
                "do not match"
            )

        # ----------------------------------
        # RULE 2: RESET SEARCH
        # ----------------------------------

        if request.intent == Intent.RESET_SEARCH:

            return RouteDecision(
                action=WorkflowAction.RESET,
                reason="user_requested_reset"
            )

        # ----------------------------------
        # RULE 3: HUMAN ASSISTANCE
        # ----------------------------------

        if request.intent == Intent.REQUEST_ASSISTANCE:

            return RouteDecision(
                action=WorkflowAction.ASSIST,
                reason="user_requested_human_help"
            )

        # ----------------------------------
        # RULE 4: CONFIRMATION RESPONSE
        # ----------------------------------

        if request.intent == Intent.CONFIRM_SEARCH:

            # User rejected the proposed search.
            if request.confirmation is False:

                return RouteDecision(
                    action=WorkflowAction.CLARIFY,
                    reason="confirmation_rejected"
                )

            # User accepted the proposal.
            if request.confirmation is True:

                # StateManager should already
                # have saved the confirmation.
                if self._is_confirmed(state):

                    return self._build_search_decision(
                        state,
                        reason="confirmation_accepted"
                    )

                # Never search if the state
                # does not contain valid
                # confirmed criteria.
                return RouteDecision(
                    action=WorkflowAction.CLARIFY,
                    reason="confirmation_not_valid",
                    missing_fields=["confirmation"]
                )

        # ----------------------------------
        # RULE 5: UNKNOWN INTENT
        # ----------------------------------

        if request.intent == Intent.UNKNOWN:

            return RouteDecision(
                action=WorkflowAction.CLARIFY,
                reason="unknown_user_intent",
                missing_fields=["intent"]
            )

        # ----------------------------------
        # RULE 6: UNSUPPORTED INTENT
        # ----------------------------------

        supported_intents = {
            Intent.FIND_HOSPITAL,
            Intent.REFINE_SEARCH
        }

        if request.intent not in supported_intents:

            return RouteDecision(
                action=WorkflowAction.CLARIFY,
                reason="unsupported_intent",
                missing_fields=["intent"]
            )

        # ----------------------------------
        # RULE 7: AMBIGUOUS INFORMATION
        # ----------------------------------

        ambiguous_fields = []

        if request.service_ambiguous:
            ambiguous_fields.append("service")

        if request.location_ambiguous:
            ambiguous_fields.append("location")

        if ambiguous_fields:

            return RouteDecision(
                action=WorkflowAction.CLARIFY,
                reason="ambiguous_search_information",
                missing_fields=ambiguous_fields
            )

        # ----------------------------------
        # RULE 8: MISSING INFORMATION
        # ----------------------------------

        missing_fields = []

        if not state.service or not state.service.strip():
            missing_fields.append("service")

        if not state.location or not state.location.strip():
            missing_fields.append("location")

        if missing_fields:

            return RouteDecision(
                action=WorkflowAction.CLARIFY,
                reason="missing_search_information",
                missing_fields=missing_fields
            )

        # ----------------------------------
        # RULE 9: PENDING CONFIRMATION
        # ----------------------------------

        pending = state.pending_confirmation

        if pending is not None:

            # Protect against an outdated
            # confirmation proposal.
            if (
                pending.service != state.service
                or pending.location != state.location
            ):

                return RouteDecision(
                    action=WorkflowAction.CLARIFY,
                    reason="outdated_confirmation",
                    missing_fields=["confirmation"]
                )

            # Search criteria exist, but the
            # user has not confirmed them.
            return RouteDecision(
                action=WorkflowAction.CONFIRM,
                reason="search_confirmation_required",
                confirmation=SearchConfirmation(
                service=pending.service,
                location=pending.location,
                confirmation_id=pending.confirmation_id
                )
            )

        # ----------------------------------
        # RULE 10: CONFIRMED SEARCH
        # ----------------------------------

        if self._is_confirmed(state):

            return self._build_search_decision(
                state,
                reason="search_criteria_confirmed"
            )

        # ----------------------------------
        # RULE 11: UNCONFIRMED SEARCH
        # ----------------------------------

        # This may happen after a user
        # rejected confirmation.

        # We must not search simply because
        # service and location are present.
        return RouteDecision(
            action=WorkflowAction.CLARIFY,
            reason="search_not_confirmed",
            missing_fields=["confirmation"]
        )

    # ==========================================
    # 4. CHECK CONFIRMED SEARCH
    # ==========================================

    def _is_confirmed(
        self,
        state: SessionState
    ) -> bool:
        """
        Return True only when the current
        service and location match the
        user's confirmed search criteria.
        """

        if not state.service or not state.location:
            return False

        if state.pending_confirmation is not None:
            return False

        return (
            state.service == state.confirmed_service
            and state.location == state.confirmed_location
        )

    # ==========================================
    # 5. BUILD SEARCH DECISION
    # ==========================================

    def _build_search_decision(
        self,
        state: SessionState,
        reason: str
    ) -> RouteDecision:
        """
        Build a structured SearchQuery.

        This function is only called after
        search criteria are confirmed.
        """

        if not self._is_confirmed(state):

            raise ValueError(
                "Cannot search with unconfirmed criteria"
            )

        search_query = SearchQuery(
            service=state.service,
            location=state.location,
            preferences=state.preferences.copy()
        )

        return RouteDecision(
            action=WorkflowAction.SEARCH,
            reason=reason,
            search_query=search_query
        )
