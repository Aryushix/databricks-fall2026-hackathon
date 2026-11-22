
"""
tests/test_orchestrator.py

Automated tests for the Care Finder orchestrator.

All facility information is FICTIONAL.

Run from the orchestrator project directory:

    python -m pytest -v

These tests exercise the existing six-file MVP.
"""

import pytest

from schemas import (
    Intent,
    UserRequest,
    ResponseStatus,
    EvidenceStatus,
)

from main import ApplicationOrchestrator

from mock_services import (
    MockSearchService,
    MockRankingService,
)

from schemas import SearchQuery


# ==========================================
# TEST FIXTURE
# ==========================================

@pytest.fixture
def setup_app():
    """
    Give every test a fresh orchestrator
    and search service.

    This prevents test sessions from
    interfering with one another.
    """

    search_service = MockSearchService()

    app = ApplicationOrchestrator(
        search_service=search_service,
        demo_mode=True
    )

    return app, search_service


def start_search(
    app,
    session_id="test_session",
    service="dialysis",
    location="Gaya"
):
    """
    Helper: start a new facility search.
    """

    return app.process(
        UserRequest(
            session_id=session_id,
            intent=Intent.FIND_HOSPITAL,
            service=service,
            location=location
        )
    )


def confirm_search(
    app,
    session_id="test_session",
    accepted=True,
    confirmation_id=None
):
    """
    Confirm the current proposal, or optionally
    send a specific ID to test stale responses.
    """

    if confirmation_id is None:

        state = app.state_manager.get(session_id)

        if (
            state is not None
            and state.pending_confirmation is not None
        ):
            confirmation_id = (
                state.pending_confirmation.confirmation_id
            )

        else:
            confirmation_id = "no-pending-proposal"

    return app.process(
        UserRequest(
            session_id=session_id,
            intent=Intent.CONFIRM_SEARCH,
            confirmation=accepted,
            confirmation_id=confirmation_id
        )
    )


# ==========================================
# TEST 1: CONFIRMATION REQUIRED
# ==========================================

def test_search_requires_confirmation(setup_app):

    app, search_service = setup_app

    response = start_search(app)

    assert (
        response.status
        == ResponseStatus.CONFIRMATION_REQUIRED
    )

    assert response.confirmation.service == "dialysis"
    assert response.confirmation.location == "Gaya"

    # Critical: no search before confirmation.
    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 2: CONFIRMATION TRIGGERS SEARCH
# ==========================================

def test_confirmation_triggers_search(setup_app):

    app, search_service = setup_app

    start_search(app)

    response = confirm_search(app)

    assert response.status == ResponseStatus.RESULTS

    assert len(search_service.search_calls) == 1

    assert len(response.facilities) == 2

    assert response.answer_card is not None

    assert (
        response.answer_card.primary_facility.facility_id
        == "DEMO-GAYA-001"
    )


# ==========================================
# TEST 3: MISSING LOCATION
# ==========================================

def test_missing_location_requests_clarification(
    setup_app
):

    app, search_service = setup_app

    response = app.process(
        UserRequest(
            session_id="missing_location",
            intent=Intent.FIND_HOSPITAL,
            service="dialysis"
        )
    )

    assert (
        response.status
        == ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert "location" in response.missing_fields

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 4: FOLLOW-UP PRESERVES CONTEXT
# ==========================================

def test_followup_preserves_previous_information(
    setup_app
):

    app, _ = setup_app

    first = app.process(
        UserRequest(
            session_id="followup",
            intent=Intent.FIND_HOSPITAL,
            location="Patna"
        )
    )

    assert first.status == ResponseStatus.CLARIFICATION_REQUIRED

    second = app.process(
        UserRequest(
            session_id="followup",
            intent=Intent.REFINE_SEARCH,
            service="dialysis"
        )
    )

    assert (
        second.status
        == ResponseStatus.CONFIRMATION_REQUIRED
    )

    assert second.confirmation.service == "dialysis"
    assert second.confirmation.location == "Patna"


# ==========================================
# TEST 5: LOCATION CHANGE REQUIRES CONFIRMATION
# ==========================================

def test_changed_location_requires_reconfirmation(
    setup_app
):

    app, search_service = setup_app

    start_search(app)
    confirm_search(app)

    assert len(search_service.search_calls) == 1

    response = app.process(
        UserRequest(
            session_id="test_session",
            intent=Intent.REFINE_SEARCH,
            location="Jaipur"
        )
    )

    assert (
        response.status
        == ResponseStatus.CONFIRMATION_REQUIRED
    )

    assert response.confirmation.location == "Jaipur"

    # Changing the location must not
    # immediately trigger another search.
    assert len(search_service.search_calls) == 1

    assert response.context.confirmed_location is None


# ==========================================
# TEST 6: PREFERENCE CHANGE NEEDS NO RECONFIRM
# ==========================================

def test_preference_change_preserves_confirmation(
    setup_app
):

    app, search_service = setup_app

    start_search(app)
    confirm_search(app)

    response = app.process(
        UserRequest(
            session_id="test_session",
            intent=Intent.REFINE_SEARCH,
            preferences={
                "sort_by": "distance"
            }
        )
    )

    assert response.status == ResponseStatus.RESULTS

    assert response.context.confirmed_service == "dialysis"
    assert response.context.confirmed_location == "Gaya"

    assert len(search_service.search_calls) == 2

    assert (
        search_service.search_calls[-1]
        .preferences["sort_by"]
        == "distance"
    )


# ==========================================
# TEST 7: REJECTED CONFIRMATION
# ==========================================

def test_rejected_confirmation_prevents_search(
    setup_app
):

    app, search_service = setup_app

    start_search(app)

    response = confirm_search(
        app,
        accepted=False
    )

    assert (
        response.status
        == ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert response.facilities == []

    assert len(search_service.search_calls) == 0

    assert response.context.confirmed_service is None


# ==========================================
# TEST 8: AMBIGUITY PREVENTS SEARCH
# ==========================================

def test_ambiguous_location_is_not_accepted(
    setup_app
):

    app, search_service = setup_app

    response = app.process(
        UserRequest(
            session_id="ambiguous",
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Patna",
            location_ambiguous=True
        )
    )

    assert (
        response.status
        == ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert "location" in response.missing_fields

    assert response.context.location is None

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 9: NO RESULTS
# ==========================================

def test_no_results_are_handled(setup_app):

    app, _ = setup_app

    start_search(
        app,
        session_id="no_results",
        service="burn care",
        location="Gaya"
    )

    response = confirm_search(
        app,
        session_id="no_results"
    )

    assert response.status == ResponseStatus.NO_RESULTS

    assert response.facilities == []

    assert response.context.last_facility_ids == []


# ==========================================
# TEST 10: MOCK RANKING
# ==========================================

def test_ranking_excludes_contradictory_evidence():

    search_service = MockSearchService()

    ranking_service = MockRankingService()

    query = SearchQuery(
        service="dialysis",
        location="Gaya"
    )

    candidates = search_service.search(query)

    assert len(candidates) == 3

    assert any(
        evidence.status == EvidenceStatus.CONTRADICTORY
        for hospital in candidates
        for evidence in hospital.evidence
    )

    ranked = ranking_service.rank(
        candidates,
        query
    )

    assert len(ranked) == 2

    assert [
        hospital.facility_id
        for hospital in ranked
    ] == [
        "DEMO-GAYA-001",
        "DEMO-GAYA-002"
    ]

    assert ranked[0].score == 86.0
    assert ranked[1].score == 75.0


# ==========================================
# TEST 11: REPRESENTATIVE ASSISTANCE
# ==========================================

def test_assistance_request_is_reused(setup_app):

    app, search_service = setup_app

    request = UserRequest(
        session_id="help_session",
        intent=Intent.REQUEST_ASSISTANCE
    )

    first = app.process(request)
    second = app.process(request)

    assert (
        first.status
        == ResponseStatus.ASSISTANCE_REQUESTED
    )

    assert (
        first.assistance_session_id
        == second.assistance_session_id
    )

    assert (
        first.assistance_session_id.startswith(
            "DEMO-HELP-"
        )
    )

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 12: BACKEND ERROR PRESERVES CONTEXT
# ==========================================

def test_backend_failure_preserves_search_context():

    search_service = MockSearchService(fail=True)

    app = ApplicationOrchestrator(
        search_service=search_service
    )

    start_search(
        app,
        session_id="failure"
    )

    response = confirm_search(
        app,
        session_id="failure"
    )

    assert response.status == ResponseStatus.ERROR

    assert response.context.confirmed_service == "dialysis"
    assert response.context.confirmed_location == "Gaya"

    # Simulate backend recovery.
    search_service.fail = False

    retry = app.process(
        UserRequest(
            session_id="failure",
            intent=Intent.REFINE_SEARCH,
            preferences={
                "sort_by": "distance"
            }
        )
    )

    assert retry.status == ResponseStatus.RESULTS


# ==========================================
# TEST 13: SESSION ISOLATION
# ==========================================

def test_sessions_do_not_share_search_context(
    setup_app
):

    app, search_service = setup_app

    start_search(
        app,
        session_id="user_A",
        service="dialysis",
        location="Gaya"
    )

    start_search(
        app,
        session_id="user_B",
        service="mri",
        location="Jaipur"
    )

    response_a = confirm_search(
        app,
        session_id="user_A"
    )

    state_b = app.state_manager.get(
        "user_B"
    )

    assert response_a.status == ResponseStatus.RESULTS

    assert state_b.service == "mri"
    assert state_b.location == "Jaipur"

    assert state_b.confirmed_service is None

    assert state_b.pending_confirmation is not None

    assert len(search_service.search_calls) == 1


# ==========================================
# TEST 14: RESET BLOCKS OLD CONFIRMATION
# ==========================================

def test_reset_invalidates_pending_confirmation(
    setup_app
):

    app, search_service = setup_app

    start_search(app)

    reset = app.process(
        UserRequest(
            session_id="test_session",
            intent=Intent.RESET_SEARCH
        )
    )

    assert reset.status == ResponseStatus.RESET
    assert reset.context.service is None
    assert reset.context.pending_confirmation is None

    old_yes = confirm_search(app)

    assert (
        old_yes.status
        == ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 15: KNOWN CONFIRMATION LIMITATION
# ==========================================

def test_old_yes_cannot_confirm_a_newer_proposal(
    setup_app
):

    app, search_service = setup_app

    first = start_search(app)

    old_id = first.confirmation.confirmation_id

    newer = app.process(
        UserRequest(
            session_id="test_session",
            intent=Intent.REFINE_SEARCH,
            location="Jaipur"
        )
    )

    new_id = newer.confirmation.confirmation_id

    assert old_id != new_id

    stale = confirm_search(
        app,
        confirmation_id=old_id
    )

    assert (
        stale.status
        == ResponseStatus.CONFIRMATION_REQUIRED
    )

    assert len(search_service.search_calls) == 0

    assert (
        app.state_manager.get("test_session")
        .pending_confirmation.confirmation_id
        == new_id
    )
