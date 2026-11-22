
"""
tests/test_api.py

Care Finder — FastAPI Automated Tests

Tests:
- API health endpoint
- JSON request validation
- Search confirmation workflow
- Multi-turn session memory
- Search refinement and corrections
- No-results handling
- Representative assistance
- Backend failure and recovery
- Session separation
- Reset behavior
- Local frontend CORS configuration

All hospital data is fictional.

Run from the orchestrator directory:

    python -m pytest tests/test_api.py -v

No real server or dataset is required.
"""

import pytest

from fastapi.testclient import TestClient

import api as api_module

from main import ApplicationOrchestrator
from mock_services import MockSearchService


# ==========================================
# 1. TEST FIXTURE
# ==========================================

@pytest.fixture
def test_env(monkeypatch):
    """
    Each test receives a fresh orchestrator.

    This prevents previous tests from
    affecting the current test's sessions.

    monkeypatch temporarily replaces the
    global orchestrator used by api.py.
    """

    search_service = MockSearchService()

    backend = ApplicationOrchestrator(
        search_service=search_service,
        demo_mode=True
    )

    monkeypatch.setattr(
        api_module,
        "orchestrator",
        backend
    )

    with TestClient(api_module.app) as client:
        yield client, search_service, backend


# ==========================================
# 2. REQUEST HELPERS
# ==========================================

def send(
    client,
    session_id,
    intent,
    **fields
):
    """
    Send a structured request through HTTP.

    Equivalent to a frontend POST request.
    """

    payload = {
        "session_id": session_id,
        "intent": intent,
        **fields
    }

    return client.post(
        "/api/process",
        json=payload
    )


def start_search(
    client,
    session_id="test_001",
    service="dialysis",
    location="Gaya"
):
    """
    Submit an initial search request.
    """

    return send(
        client,
        session_id,
        "find_hospital",
        service=service,
        location=location
    )


def confirm_search(
    client,
    session_id="test_001",
    accepted=True,
    confirmation_id=None
):
    """
    Submit a confirmation through HTTP.

    Existing tests can use the current ID.
    Stale-ID tests can pass an older ID.
    """

    if confirmation_id is None:

        state = (
            api_module.orchestrator
            .state_manager.get(session_id)
        )

        if (
            state is not None
            and state.pending_confirmation is not None
        ):
            confirmation_id = (
                state.pending_confirmation.confirmation_id
            )

        else:
            confirmation_id = "no-pending-proposal"

    return send(
        client,
        session_id,
        "confirm_search",
        confirmation=accepted,
        confirmation_id=confirmation_id
    )


# ==========================================
# TEST 1: HEALTH ENDPOINT
# ==========================================

def test_health_endpoint(test_env):

    client, _, _ = test_env

    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"

    assert data["service"] == "care-finder-api"

    assert data["mode"] == "demo"


# ==========================================
# TEST 2: INITIAL SEARCH REQUIRES CONFIRMATION
# ==========================================

def test_search_requires_confirmation(test_env):

    client, search_service, _ = test_env

    response = start_search(client)

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "confirmation_required"

    assert data["confirmation"] == {
        "service": "dialysis",
        "location": "Gaya"
    }

    assert data["facilities"] == []

    # Critical: no search before confirmation.
    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 3: CONFIRMATION RETURNS RESULTS
# ==========================================

def test_confirmation_returns_hospitals(test_env):

    client, search_service, _ = test_env

    first = start_search(client)

    assert first.json()["status"] == "confirmation_required"

    response = confirm_search(client)

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "results"

    assert len(data["facilities"]) == 2

    assert len(search_service.search_calls) == 1

    # Verify mock results remain clearly fictional.
    assert (
        data["facilities"][0]["facility_id"]
        == "DEMO-GAYA-001"
    )

    assert (
        data["facilities"][0]["evidence"][0]["text"]
        .startswith("[FICTIONAL]")
    )

    # Verify shared answer card.
    card = data["answer_card"]

    assert card is not None

    assert (
        card["primary_facility"]["facility_id"]
        == "DEMO-GAYA-001"
    )

    assert len(card["backup_facilities"]) == 1

    assert card["call_ahead"] is True


# ==========================================
# TEST 4: MISSING LOCATION
# ==========================================

def test_missing_location_returns_clarification(
    test_env
):

    client, search_service, _ = test_env

    response = send(
        client,
        "missing_location",
        "find_hospital",
        service="dialysis"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "clarification_required"

    assert "location" in data["missing_fields"]

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 5: MULTI-TURN SESSION MEMORY
# ==========================================

def test_followup_remembers_previous_location(
    test_env
):

    client, _, _ = test_env

    first = send(
        client,
        "followup",
        "find_hospital",
        location="Patna"
    )

    assert first.json()["status"] == "clarification_required"

    second = send(
        client,
        "followup",
        "refine_search",
        service="dialysis"
    )

    data = second.json()

    assert data["status"] == "confirmation_required"

    assert data["confirmation"]["service"] == "dialysis"

    assert data["confirmation"]["location"] == "Patna"


# ==========================================
# TEST 6: PREFERENCE UPDATE
# ==========================================

def test_preference_update_does_not_reconfirm(
    test_env
):

    client, search_service, _ = test_env

    start_search(client)
    confirm_search(client)

    response = send(
        client,
        "test_001",
        "refine_search",
        preferences={
            "sort_by": "distance"
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "results"

    assert len(search_service.search_calls) == 2

    assert (
        search_service.search_calls[-1]
        .preferences["sort_by"]
        == "distance"
    )

    assert (
        data["context"]["confirmed_location"]
        == "Gaya"
    )


# ==========================================
# TEST 7: LOCATION CHANGE REQUIRES RECONFIRMATION
# ==========================================

def test_location_change_requires_confirmation(
    test_env
):

    client, search_service, _ = test_env

    start_search(client)
    confirm_search(client)

    assert len(search_service.search_calls) == 1

    response = send(
        client,
        "test_001",
        "refine_search",
        location="Jaipur"
    )

    data = response.json()

    assert data["status"] == "confirmation_required"

    assert data["confirmation"]["location"] == "Jaipur"

    assert data["context"]["confirmed_location"] is None

    # No second search before confirmation.
    assert len(search_service.search_calls) == 1

    confirmed = confirm_search(client)

    assert confirmed.json()["status"] == "results"

    assert len(search_service.search_calls) == 2

    assert (
        search_service.search_calls[-1].location
        == "Jaipur"
    )


# ==========================================
# TEST 8: REJECTED CONFIRMATION
# ==========================================

def test_rejected_confirmation_does_not_search(
    test_env
):

    client, search_service, _ = test_env

    start_search(client)

    response = confirm_search(
        client,
        accepted=False
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "clarification_required"

    assert data["facilities"] == []

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 9: INVALID CONFIRMATION JSON
# ==========================================

def test_missing_confirmation_value_returns_422(
    test_env
):

    client, _, _ = test_env

    response = send(
        client,
        "invalid_confirmation",
        "confirm_search"
    )

    assert response.status_code == 422

    assert "detail" in response.json()


# ==========================================
# TEST 10: INVALID INTENT
# ==========================================

def test_invalid_intent_returns_422(test_env):

    client, _, _ = test_env

    response = send(
        client,
        "invalid_intent",
        "fly_to_moon"
    )

    assert response.status_code == 422


# ==========================================
# TEST 11: INVALID SESSION ID
# ==========================================

def test_blank_session_id_returns_422(test_env):

    client, _, _ = test_env

    response = send(
        client,
        "   ",
        "find_hospital",
        service="dialysis",
        location="Gaya"
    )

    assert response.status_code == 422


# ==========================================
# TEST 12: CONFIRMATION WITHOUT PENDING SEARCH
# ==========================================

def test_confirmation_without_pending_search(
    test_env
):

    client, search_service, _ = test_env

    response = confirm_search(
        client,
        session_id="never_started"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "clarification_required"

    assert data["facilities"] == []

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 13: NO RESULTS
# ==========================================

def test_no_results_response(test_env):

    client, _, _ = test_env

    start_search(
        client,
        session_id="no_results",
        service="burn care",
        location="Gaya"
    )

    response = confirm_search(
        client,
        session_id="no_results"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "no_results"

    assert data["facilities"] == []

    assert data["answer_card"] is None

    assert data["context"]["last_facility_ids"] == []


# ==========================================
# TEST 14: REPRESENTATIVE ASSISTANCE
# ==========================================

def test_assistance_request(test_env):

    client, search_service, _ = test_env

    first = send(
        client,
        "assistance_001",
        "request_assistance"
    )

    assert first.status_code == 200

    first_data = first.json()

    assert first_data["status"] == "assistance_requested"

    assistance_id = first_data["assistance_session_id"]

    assert assistance_id.startswith("DEMO-HELP-")

    # A repeated request reuses the same ID.
    second = send(
        client,
        "assistance_001",
        "request_assistance"
    )

    assert (
        second.json()["assistance_session_id"]
        == assistance_id
    )

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 15: BACKEND FAILURE AND RECOVERY
# ==========================================

def test_backend_failure_preserves_session(
    test_env
):

    client, search_service, _ = test_env

    search_service.fail = True

    start_search(
        client,
        session_id="backend_failure"
    )

    response = confirm_search(
        client,
        session_id="backend_failure"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "error"

    assert data["context"]["confirmed_service"] == "dialysis"

    assert data["context"]["confirmed_location"] == "Gaya"

    # Simulate the backend recovering.
    search_service.fail = False

    retry = send(
        client,
        "backend_failure",
        "refine_search",
        preferences={
            "sort_by": "distance"
        }
    )

    assert retry.status_code == 200

    assert retry.json()["status"] == "results"


# ==========================================
# TEST 16: SESSION SEPARATION
# ==========================================

def test_two_sessions_keep_separate_context(
    test_env
):

    client, search_service, _ = test_env

    start_search(
        client,
        session_id="session_A",
        service="dialysis",
        location="Gaya"
    )

    start_search(
        client,
        session_id="session_B",
        service="mri",
        location="Jaipur"
    )

    # Confirm only session A.
    response_a = confirm_search(
        client,
        session_id="session_A"
    )

    assert response_a.json()["status"] == "results"

    # Session B still has its own proposal.
    response_b = send(
        client,
        "session_B",
        "refine_search"
    )

    data_b = response_b.json()

    assert data_b["status"] == "confirmation_required"

    assert data_b["confirmation"]["service"] == "mri"

    assert data_b["confirmation"]["location"] == "Jaipur"

    # Only session A searched.
    assert len(search_service.search_calls) == 1


# ==========================================
# TEST 17: RESET INVALIDATES CONFIRMATION
# ==========================================

def test_reset_invalidates_old_confirmation(
    test_env
):

    client, search_service, _ = test_env

    start_search(
        client,
        session_id="reset_test"
    )

    reset = send(
        client,
        "reset_test",
        "reset_search"
    )

    assert reset.status_code == 200

    reset_data = reset.json()

    assert reset_data["status"] == "reset"

    assert reset_data["context"]["service"] is None

    assert (
        reset_data["context"]["pending_confirmation"]
        is None
    )

    # A Yes after reset cannot run a search.
    old_yes = confirm_search(
        client,
        session_id="reset_test"
    )

    assert (
        old_yes.json()["status"]
        == "clarification_required"
    )

    assert len(search_service.search_calls) == 0


# ==========================================
# TEST 18: LOCAL FRONTEND CORS
# ==========================================

def test_local_frontend_cors(test_env):

    client, _, _ = test_env

    response = client.options(
        "/api/process",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"
        }
    )

    assert response.status_code == 200

    assert (
        response.headers.get(
            "access-control-allow-origin"
        )
        == "http://localhost:3000"
    )

# ==========================================
# TEST 19: STALE-ID TEST
# ==========================================

def test_stale_confirmation_id_is_rejected(test_env):

    client, search_service, _ = test_env

    session = "stale_id_test"

    # First proposal: dialysis near Gaya.
    first = start_search(
        client,
        session_id=session,
        service="dialysis",
        location="Gaya"
    )

    old_id = (
        first.json()["confirmation"]["confirmation_id"]
    )

    # User changes location before confirming.
    second = send(
        client,
        session,
        "refine_search",
        location="Jaipur"
    )

    new_id = (
        second.json()["confirmation"]["confirmation_id"]
    )

    # The new proposal must have a different ID.
    assert old_id != new_id

    # An old Yes arrives for the Gaya proposal.
    stale_response = confirm_search(
        client,
        session_id=session,
        accepted=True,
        confirmation_id=old_id
    )

    stale_data = stale_response.json()

    # Must NOT initiate search.
    assert (
        stale_data["status"]
        == "confirmation_required"
    )

    assert len(search_service.search_calls) == 0

    # The Jaipur proposal must remain active.
    assert (
        stale_data["confirmation"]["confirmation_id"]
        == new_id
    )

    assert (
        stale_data["confirmation"]["location"]
        == "Jaipur"
    )

    # Now confirm the actual current proposal.
    valid_response = confirm_search(
        client,
        session_id=session,
        accepted=True,
        confirmation_id=new_id
    )

    assert valid_response.json()["status"] == "results"

    assert len(search_service.search_calls) == 1

    assert (
        search_service.search_calls[0].location
        == "Jaipur"
    )
