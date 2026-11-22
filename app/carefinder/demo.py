
"""
demo.py

Care Finder — Terminal Orchestrator Demonstration

Demonstrates the complete application workflow
without a chatbot, dataset, RAG engine, or UI.

The user's example messages below are ONLY
illustrative. We manually construct UserRequest
objects to simulate the output of an intent module.

ALL HOSPITAL DATA, SCORES, AND DISTANCES
ARE FICTIONAL AND NOT FOR PATIENT USE.

Run:
    python demo.py

Run an individual scenario:
    python demo.py --scenario 1

No external packages required.
"""

import argparse

from main import ApplicationOrchestrator
from mock_services import MockSearchService
from schemas import (
    Intent,
    ResponseStatus,
    UserRequest,
)


def make_confirmation_request(
    app,
    session_id,
    accepted
):
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

    return UserRequest(
        session_id=session_id,
        intent=Intent.CONFIRM_SEARCH,
        confirmation=accepted,
        confirmation_id=confirmation_id
    )


# ==========================================
# 1. TERMINAL DISPLAY HELPERS
# ==========================================

def heading(title):
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)


def show_response(response):
    """
    Print the orchestrator's structured
    response in a readable format.
    """

    print("\nORCHESTRATOR RESPONSE")
    print("-" * 45)

    print("Status:", response.status.value)
    print("Message:", response.message)

    # Show confirmation details.
    if response.confirmation is not None:

        print("\nPending confirmation:")
        print(
            "  Service:",
            response.confirmation.service
        )
        print(
            "  Location:",
            response.confirmation.location
        )

    # Show missing information.
    if response.missing_fields:

        print(
            "\nNeeds clarification:",
            ", ".join(response.missing_fields)
        )

    # Show fictional hospital results.
    if response.facilities:

        print("\nFICTIONAL FACILITY RESULTS")
        print("Not for real healthcare decisions.")

        for index, hospital in enumerate(
            response.facilities,
            start=1
        ):

            print(
                f"\n  {index}. {hospital.name}"
            )

            print(
                "     Facility ID:",
                hospital.facility_id
            )

            print(
                "     City:",
                hospital.city
            )

            print(
                "     Demo distance:",
                hospital.distance_km,
                "km"
            )

            print(
                "     Demo score:",
                hospital.score
            )

            print(
                "     Score breakdown:",
                hospital.score_breakdown
            )

            if hospital.trust_label is not None:

                print(
                    "     Trust label:",
                    hospital.trust_label.value
                )

            for evidence in hospital.evidence:

                print(
                    "     Evidence:",
                    evidence.text
                )

                print(
                    "     Evidence field:",
                    evidence.source_field
                )

                print(
                    "     Source URL:",
                    evidence.source_url
                    or "None — fictional demo"
                )

    # Show answer card.
    if response.answer_card is not None:

        card = response.answer_card

        print("\nSHARED ANSWER CARD")

        print(
            "  Primary facility:",
            card.primary_facility.name
        )

        print(
            "  Backup facilities:",
            len(card.backup_facilities)
        )

        print(
            "  Call ahead:",
            card.call_ahead
        )

    # Show representative-assistance ID.
    if response.assistance_session_id:

        print(
            "\nAssistance session:",
            response.assistance_session_id
        )

    # Show current conversation memory.
    if response.context is not None:

        state = response.context

        print("\nSESSION MEMORY")

        print("  Service:", state.service)
        print("  Location:", state.location)

        print(
            "  Confirmed service:",
            state.confirmed_service
        )

        print(
            "  Confirmed location:",
            state.confirmed_location
        )

        print(
            "  Preferences:",
            state.preferences
        )

        print(
            "  Last facility IDs:",
            state.last_facility_ids
        )

    print("-" * 45)


# ==========================================
# 2. SIMULATE AN INTERPRETED USER MESSAGE
# ==========================================

def send(
    app,
    example_user_message,
    request,
    expected_status
):
    """
    Simulate a request received from the
    future chatbot / intent-understanding module.

    Verify that the orchestrator returns
    the expected response status.
    """

    print("\nUSER:", example_user_message)

    print("\nSIMULATED INTENT MODULE OUTPUT")
    print("  Intent:", request.intent.value)
    print("  Service:", request.service)
    print("  Location:", request.location)
    print("  Confirmation:", request.confirmation)
    print("  Preferences:", request.preferences)

    response = app.process(request)

    show_response(response)

    assert response.status == expected_status, (
        f"Expected {expected_status.value}, "
        f"received {response.status.value}"
    )

    return response


# ==========================================
# 3. SCENARIO 1: COMPLETE SEARCH
# ==========================================

def scenario_1():

    heading("SCENARIO 1 — CONFIRMATION BEFORE SEARCH")

    search_service = MockSearchService()

    app = ApplicationOrchestrator(
        search_service=search_service
    )

    session = "demo_001"

    # User asks for a hospital.
    response1 = send(
        app,
        "Find dialysis near Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    # Confirm no hospital search happened yet.
    assert len(search_service.search_calls) == 0

    assert response1.confirmation is not None

    print(
        "\nCHECK: No search was executed "
        "before confirmation."
    )

    # User confirms the interpretation.
    response2 = send(
        app,
        "Yes, that's correct.",
        make_confirmation_request(app, session, True),
        ResponseStatus.RESULTS
    )

    assert len(search_service.search_calls) == 1

    assert response2.answer_card is not None

    print(
        "\nCHECK: Search ran only after confirmation."
    )


# ==========================================
# 4. SCENARIO 2: MISSING INFORMATION
# ==========================================

def scenario_2():

    heading("SCENARIO 2 — MISSING INFO AND FOLLOW-UP")

    app = ApplicationOrchestrator()

    session = "demo_002"

    # User provides a location but no service.
    send(
        app,
        "Find a hospital near Patna.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            location="Patna"
        ),
        ResponseStatus.CLARIFICATION_REQUIRED
    )

    # User adds only the missing service.
    send(
        app,
        "Dialysis.",
        UserRequest(
            session_id=session,
            intent=Intent.REFINE_SEARCH,
            service="dialysis"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    # User confirms the combined information.
    send(
        app,
        "Yes.",
        make_confirmation_request(app, session, True),
        ResponseStatus.RESULTS
    )

    # User changes a preference only.
    response = send(
        app,
        "Show the closest options.",
        UserRequest(
            session_id=session,
            intent=Intent.REFINE_SEARCH,
            preferences={
                "sort_by": "distance"
            }
        ),
        ResponseStatus.RESULTS
    )

    assert response.context.service == "dialysis"
    assert response.context.location == "Patna"

    print(
        "\nCHECK: Service and location were "
        "remembered across multiple turns."
    )


# ==========================================
# 5. SCENARIO 3: REJECT AND CORRECT
# ==========================================

def scenario_3():

    heading("SCENARIO 3 — REJECTED CONFIRMATION")

    app = ApplicationOrchestrator()

    session = "demo_003"

    send(
        app,
        "Dialysis near Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    # User says the location is wrong.
    send(
        app,
        "No, that's not right.",
        make_confirmation_request(app, session, False),
        ResponseStatus.CLARIFICATION_REQUIRED
    )

    # User corrects just the location.
    response = send(
        app,
        "I meant Jaipur.",
        UserRequest(
            session_id=session,
            intent=Intent.REFINE_SEARCH,
            location="Jaipur"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    assert response.confirmation.service == "dialysis"
    assert response.confirmation.location == "Jaipur"

    # User confirms corrected search.
    send(
        app,
        "Yes, Jaipur.",
        make_confirmation_request(app, session, True),
        ResponseStatus.RESULTS
    )

    print(
        "\nCHECK: Correction retained the service "
        "and required a new confirmation."
    )


# ==========================================
# 6. SCENARIO 4: AMBIGUOUS LOCATION
# ==========================================

def scenario_4():

    heading("SCENARIO 4 — AMBIGUOUS INFORMATION")

    app = ApplicationOrchestrator()

    session = "demo_004"

    response = send(
        app,
        "I need dialysis near a place I mentioned.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Patna",
            location_ambiguous=True
        ),
        ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert response.context.location is None

    # Clarify the location.
    send(
        app,
        "I mean Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.REFINE_SEARCH,
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    print(
        "\nCHECK: Ambiguous location was not "
        "accepted as confirmed information."
    )


# ==========================================
# 7. SCENARIO 5: NO RESULTS
# ==========================================

def scenario_5():

    heading("SCENARIO 5 — NO MATCHING DEMO FACILITIES")

    app = ApplicationOrchestrator()

    session = "demo_005"

    send(
        app,
        "Find burn care near Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="burn care",
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    response = send(
        app,
        "Yes.",
        make_confirmation_request(app, session, True),
        ResponseStatus.NO_RESULTS
    )

    assert response.facilities == []

    print(
        "\nCHECK: Empty mock results were handled."
    )

    print(
        "This does NOT mean real facilities "
        "are unavailable in Gaya."
    )


# ==========================================
# 8. SCENARIO 6: REPRESENTATIVE ASSISTANCE
# ==========================================

def scenario_6():

    heading("SCENARIO 6 — HUMAN REPRESENTATIVE HELP")

    app = ApplicationOrchestrator()

    session = "demo_006"

    response1 = send(
        app,
        "I want someone to help me use the app.",
        UserRequest(
            session_id=session,
            intent=Intent.REQUEST_ASSISTANCE
        ),
        ResponseStatus.ASSISTANCE_REQUESTED
    )

    # Repeating the request returns the same
    # mock assistance session.
    response2 = send(
        app,
        "Can you connect me to a representative?",
        UserRequest(
            session_id=session,
            intent=Intent.REQUEST_ASSISTANCE
        ),
        ResponseStatus.ASSISTANCE_REQUESTED
    )

    assert (
        response1.assistance_session_id
        == response2.assistance_session_id
    )

    print(
        "\nCHECK: Representative request was "
        "simulated without duplicate sessions."
    )

    print(
        "No real person was contacted."
    )


# ==========================================
# 9. SCENARIO 7: BACKEND FAILURE AND RETRY
# ==========================================

def scenario_7():

    heading("SCENARIO 7 — BACKEND FAILURE AND RETRY")

    search_service = MockSearchService(
        fail=True
    )

    app = ApplicationOrchestrator(
        search_service=search_service
    )

    session = "demo_007"

    send(
        app,
        "Find dialysis near Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    # Confirming triggers the failing service.
    response = send(
        app,
        "Yes.",
        make_confirmation_request(app, session, True),
        ResponseStatus.ERROR
    )

    assert response.context.confirmed_service == "dialysis"
    assert response.context.confirmed_location == "Gaya"

    print(
        "\nCHECK: Search criteria survived "
        "the simulated backend error."
    )

    # Pretend the backend recovers.
    search_service.fail = False

    # Refine the confirmed search to retry it.
    send(
        app,
        "Try again with closest first.",
        UserRequest(
            session_id=session,
            intent=Intent.REFINE_SEARCH,
            preferences={
                "sort_by": "distance"
            }
        ),
        ResponseStatus.RESULTS
    )

    print(
        "\nCHECK: Retry succeeded without "
        "re-entering the service or location."
    )


# ==========================================
# 10. SCENARIO 8: RESET AND INVALID CONFIRMATION
# ==========================================

def scenario_8():

    heading("SCENARIO 8 — RESET AND INVALID CONFIRMATION")

    app = ApplicationOrchestrator()

    session = "demo_008"

    send(
        app,
        "Find dialysis near Gaya.",
        UserRequest(
            session_id=session,
            intent=Intent.FIND_HOSPITAL,
            service="dialysis",
            location="Gaya"
        ),
        ResponseStatus.CONFIRMATION_REQUIRED
    )

    response = send(
        app,
        "Start over.",
        UserRequest(
            session_id=session,
            intent=Intent.RESET_SEARCH
        ),
        ResponseStatus.RESET
    )

    assert response.context.service is None
    assert response.context.location is None
    assert response.context.pending_confirmation is None

    # An old "yes" must not authorize a search.
    response = send(
        app,
        "Yes.",
        make_confirmation_request(app, session, True),
        ResponseStatus.CLARIFICATION_REQUIRED
    )

    assert response.facilities == []

    print(
        "\nCHECK: Reset cleared search memory "
        "and prevented confirmation without "
        "a pending request."
    )


# ==========================================
# 11. SCENARIO REGISTRY
# ==========================================

SCENARIOS = {
    "1": scenario_1,
    "2": scenario_2,
    "3": scenario_3,
    "4": scenario_4,
    "5": scenario_5,
    "6": scenario_6,
    "7": scenario_7,
    "8": scenario_8,
}


# ==========================================
# 12. MAIN DEMO ENTRY POINT
# ==========================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Care Finder orchestrator "
            "terminal demonstration"
        )
    )

    parser.add_argument(
        "--scenario",
        choices=SCENARIOS.keys(),
        help="Run one scenario instead of all eight"
    )

    args = parser.parse_args()

    heading("CARE FINDER — APPLICATION ORCHESTRATOR")

    print(
        "\nSIMULATION ONLY — NO REAL HEALTHCARE DATA"
    )

    print(
        "Input is already structured as though "
        "an intent module interpreted it."
    )

    print(
        "The orchestrator manages confirmation, "
        "state, routing and mock services."
    )

    if args.scenario:

        selected = {
            args.scenario: SCENARIOS[args.scenario]
        }

    else:

        selected = SCENARIOS

    completed = 0

    for number, scenario in selected.items():

        try:

            scenario()

            completed += 1

            print(
                f"\nSCENARIO {number}: PASSED"
            )

        except Exception as error:

            print(
                f"\nSCENARIO {number}: FAILED"
            )

            print(
                f"{type(error).__name__}: {error}"
            )

            raise

    heading("DEMONSTRATION COMPLETE")

    print(
        f"\nScenarios passed: "
        f"{completed}/{len(selected)}"
    )

    print(
        "\nThe orchestrator workflow is functioning "
        "with simulated backend services."
    )

    print(
        "\nNext integration targets:"
    )

    print("  - Language / intent module")
    print("  - Real hospital retrieval")
    print("  - Evidence-based ranking")
    print("  - Persistent session storage")
    print("  - Web and voice interfaces")


if __name__ == "__main__":
    main()
