
"""
main.py

Care Finder — Application Orchestrator

Coordinates:
1. Request validation and state updates
2. Workflow routing
3. User confirmation
4. Hospital search
5. Hospital ranking
6. Representative assistance
7. Standardized application responses
8. Error handling

All external services are currently mocked.

No third-party packages required.
"""

from mock_services import (
    MockAssistanceService,
    MockRankingService,
    MockSearchService,
)
from schemas import (
    AnswerCard,
    Intent,
    OrchestratorResponse,
    ResponseStatus,
    UserRequest,
)
from state_manager import StateManager
from workflow_router import (
    WorkflowAction,
    WorkflowRouter,
)

# ==========================================
# APPLICATION ORCHESTRATOR
# ==========================================

class ApplicationOrchestrator:
    """
    Central controller for Care Finder.

    Services are injected through the constructor
    so mock implementations can later be replaced
    with real backend services.

    Input:
        UserRequest

    Output:
        OrchestratorResponse
    """

    def __init__(
        self,
        state_manager=None,
        router=None,
        search_service=None,
        ranking_service=None,
        assistance_service=None,
        demo_mode=True,
    ):

        self.state_manager = (
            state_manager
            if state_manager is not None
            else StateManager()
        )

        self.router = (
            router
            if router is not None
            else WorkflowRouter()
        )

        self.search_service = (
            search_service
            if search_service is not None
            else MockSearchService()
        )

        self.ranking_service = (
            ranking_service
            if ranking_service is not None
            else MockRankingService()
        )

        self.assistance_service = (
            assistance_service
            if assistance_service is not None
            else MockAssistanceService()
        )

        self.demo_mode = demo_mode

    # ==========================================
    # 1. MAIN ENTRY POINT
    # ==========================================

    def process(
        self,
        request: UserRequest
    ) -> OrchestratorResponse:
        """
        Process one interpreted user request.

        This is the method that future APIs
        and language modules will call.
        """

        # ----------------------------------
        # STEP 1: UPDATE SESSION STATE
        # ----------------------------------

        try:

            state = (
                self.state_manager.update_from_request(
                    request
                )
            )

            # ----------------------------------
            # STEP 2: DETERMINE WORKFLOW
            # ----------------------------------

            decision = self.router.route(
                request,
                state
            )

        except ValueError:

            # Invalid confirmation attempts
            # should not trigger hospital searches.

            if request.intent == Intent.CONFIRM_SEARCH:

                current = self.state_manager.get(
                request.session_id
            )
                # A newer confirmation is still pending.
                # Re-present it instead of losing context.
                if (
                current is not None
                and current.pending_confirmation is not None
            ):
                    pending = current.pending_confirmation
                    return OrchestratorResponse(
                    session_id=request.session_id,
                    status=ResponseStatus.CONFIRMATION_REQUIRED,
                    message=(
                        "That confirmation is outdated. "
                        f"Please confirm: {pending.service} "
                        f"near {pending.location}. "
                        "Is that correct?"
                    ),
                    confirmation=pending,
                    context=current
                )
                # There is no current proposal.
                return OrchestratorResponse(
                    session_id=request.session_id,
                    status=ResponseStatus.CLARIFICATION_REQUIRED,
                    message=(
                        "There is no current search awaiting "
                        "confirmation. Please enter the "
                        "service and location."
                    ),
                    missing_fields=["confirmation"],
                    context=current
                )


            return self._error_response(
                request.session_id,
                "The request could not be processed."
            )

        # ----------------------------------
        # STEP 3: EXECUTE WORKFLOW
        # ----------------------------------

        try:

            if decision.action == WorkflowAction.CONFIRM:

                return self._handle_confirm(
                    request,
                    decision,
                    state
                )

            if decision.action == WorkflowAction.CLARIFY:

                return self._handle_clarify(
                    request,
                    decision,
                    state
                )

            if decision.action == WorkflowAction.SEARCH:

                return self._handle_search(
                    request,
                    decision
                )

            if decision.action == WorkflowAction.ASSIST:

                return self._handle_assistance(
                    request
                )

            if decision.action == WorkflowAction.RESET:

                return self._handle_reset(
                    request
                )

            return self._error_response(
                request.session_id,
                "Unknown workflow action."
            )

        except Exception:
            # Do not expose internal backend errors
            # or credentials to the future frontend.

            return self._error_response(
                request.session_id,
                (
                    "A backend service is temporarily "
                    "unavailable. Please try again."
                )
            )

    # ==========================================
    # 2. HANDLE CONFIRMATION
    # ==========================================

    def _handle_confirm(
        self,
        request,
        decision,
        state
    ) -> OrchestratorResponse:
        """
        Ask the user to confirm the interpreted
        service and location.

        Search must not execute yet.
        """

        confirmation = decision.confirmation

        message = (
            f"I understood that you need "
            f"{confirmation.service} near "
            f"{confirmation.location}. "
            f"Is that correct?"
        )

        return OrchestratorResponse(
            session_id=request.session_id,
            status=ResponseStatus.CONFIRMATION_REQUIRED,
            message=message,
            confirmation=confirmation,
            context=state
        )

    # ==========================================
    # 3. HANDLE CLARIFICATION
    # ==========================================

    def _handle_clarify(
        self,
        request,
        decision,
        state
    ) -> OrchestratorResponse:
        """
        Request missing or corrected information.

        The language module can later translate
        or speak this response.
        """

        missing = decision.missing_fields

        if decision.reason == "confirmation_rejected":

            message = (
                "What should I change about "
                "the service or location?"
            )

        elif "service" in missing and "location" in missing:

            message = (
                "What healthcare service do you "
                "need, and which city or district "
                "should I search near?"
            )

        elif "service" in missing:

            message = (
                "What healthcare service "
                "are you looking for?"
            )

        elif "location" in missing:

            message = (
                "Which city, district or PIN code "
                "should I search near?"
            )

        elif "confirmation" in missing:

            message = (
                "Please clarify the service "
                "or location before searching."
            )

        else:

            message = (
                "Could you clarify what "
                "you would like me to do?"
            )

        return OrchestratorResponse(
            session_id=request.session_id,
            status=ResponseStatus.CLARIFICATION_REQUIRED,
            message=message,
            missing_fields=list(missing),
            context=state
        )

    # ==========================================
    # 4. HANDLE HOSPITAL SEARCH
    # ==========================================

    def _handle_search(
        self,
        request,
        decision
    ) -> OrchestratorResponse:
        """
        Execute the search and ranking workflow.

        Current:
            MockSearchService
            MockRankingService

        Future:
            Databricks RAG
            Evidence-based ranking engine
        """

        query = decision.search_query

        if query is None:
            raise ValueError(
                "Search action requires SearchQuery"
            )

        # ----------------------------------
        # STEP A: RETRIEVE CANDIDATES
        # ----------------------------------

        candidates = self.search_service.search(
            query
        )

        # ----------------------------------
        # STEP B: RANK CANDIDATES
        # ----------------------------------

        if candidates:

            ranked = self.ranking_service.rank(
                candidates,
                query
            )

        else:

            ranked = []

        # ----------------------------------
        # STEP C: HANDLE EMPTY RESULTS
        # ----------------------------------

        if not ranked:

            state = (
                self.state_manager.save_facility_ids(
                    request.session_id,
                    []
                )
            )

            return OrchestratorResponse(
                session_id=request.session_id,
                status=ResponseStatus.NO_RESULTS,
                message=(
                    "No sufficiently supported "
                    "facility results were returned "
                    "for this search."
                ),
                context=state,
                warnings=(
                    ["This search uses fictional demo data."]
                    if self.demo_mode
                    else []
                )
            )

        # ----------------------------------
        # STEP D: SELECT TOP FACILITIES
        # ----------------------------------

        top_hospitals = ranked[:3]

        # ----------------------------------
        # STEP E: SAVE RECOMMENDATIONS
        # ----------------------------------

        facility_ids = [
            hospital.facility_id
            for hospital in top_hospitals
        ]

        state = (
            self.state_manager.save_facility_ids(
                request.session_id,
                facility_ids
            )
        )

        # ----------------------------------
        # STEP F: ASSEMBLE ANSWER CARD
        # ----------------------------------

        # Collect warnings without duplicates.
        uncertainties = []

        for hospital in top_hospitals:
            for warning in hospital.warnings:

                if warning not in uncertainties:
                    uncertainties.append(warning)

        answer_card = AnswerCard(
            primary_facility=top_hospitals[0],
            backup_facilities=top_hospitals[1:],
            uncertainties=uncertainties,
            call_ahead=True
        )

        # ----------------------------------
        # STEP G: RETURN RESULTS
        # ----------------------------------

        if self.demo_mode:

            message = (
                f"DEMO ONLY: Found "
                f"{len(top_hospitals)} fictional "
                f"facility result(s) for "
                f"{query.service} near "
                f"{query.location}. "
                f"No real healthcare availability "
                f"has been verified."
            )

        else:

            message = (
                f"Found {len(top_hospitals)} "
                f"facility option(s) for "
                f"{query.service} near "
                f"{query.location}. "
                f"Review the supporting evidence "
                f"and call ahead to confirm "
                f"current availability."
            )

        return OrchestratorResponse(
            session_id=request.session_id,
            status=ResponseStatus.RESULTS,
            message=message,
            facilities=top_hospitals,
            answer_card=answer_card,
            warnings=uncertainties,
            context=state
        )

    # ==========================================
    # 5. HANDLE REPRESENTATIVE ASSISTANCE
    # ==========================================

    def _handle_assistance(
        self,
        request
    ) -> OrchestratorResponse:
        """
        Create a representative-assistance request.

        Current:
            Simulated assistance session.

        Future:
            Real representative queue and
            consent-based assistance workflow.
        """

        assistance_id = (
            self.assistance_service.request_assistance(
                request.session_id
            )
        )

        state = (
            self.state_manager.set_assistance_session(
                request.session_id,
                assistance_id
            )
        )

        if self.demo_mode:

            message = (
                "DEMO ONLY: A representative "
                "assistance request was simulated. "
                "No real representative was contacted."
            )

        else:

            message = (
                "Your request for representative "
                "assistance has been received."
            )

        return OrchestratorResponse(
            session_id=request.session_id,
            status=ResponseStatus.ASSISTANCE_REQUESTED,
            message=message,
            assistance_session_id=assistance_id,
            context=state
        )

    # ==========================================
    # 6. HANDLE SEARCH RESET
    # ==========================================

    def _handle_reset(
        self,
        request
    ) -> OrchestratorResponse:
        """
        Return the reset search state.

        StateManager already cleared the search
        when it processed RESET_SEARCH.
        """

        state = self.state_manager.get(
            request.session_id
        )

        return OrchestratorResponse(
            session_id=request.session_id,
            status=ResponseStatus.RESET,
            message=(
                "Your previous search has been "
                "cleared. What healthcare service "
                "would you like to find?"
            ),
            context=state
        )

    # ==========================================
    # 7. HANDLE ERRORS
    # ==========================================

    def _error_response(
        self,
        session_id,
        message
    ) -> OrchestratorResponse:
        """
        Return a consistent error response.

        Preserve existing conversation state.
        """

        state = self.state_manager.get(
            session_id
        )

        return OrchestratorResponse(
            session_id=session_id,
            status=ResponseStatus.ERROR,
            message=message,
            context=state
        )
