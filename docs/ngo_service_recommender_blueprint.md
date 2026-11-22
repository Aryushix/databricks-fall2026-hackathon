# NGO service recommender blueprint (proposal, not implemented)

**Status, 2026-11-22 11:25 ET.** This branch holds a teammate's blueprint for review. None of it is built yet. It
starts from `brian/demo-ready` (15 verified services, service + town/PIN search). Much of the blueprint already
exists in other forms:

| Blueprint step | What exists today | What's new |
| --- | --- | --- |
| 1. Service catalog, contract | `configs/care_needs.yaml`: 25 services, 15 with KBs (incl. NICU and MRI, which the blueprint lists as `kb_id: null`). `ServiceRequest` / `ServiceResponse`, `GET /services`, `POST /find-facilities` | `radius_km` (fixed today at 100 km, widened once to 150) and `min_confidence` as inputs; a 4-pillar requirement profile per service |
| 2. Claim verification, source trust | The treatment verifier does this for the 15 KB services: official-page check, copies (`copies.py`), negations / outsourcing, and a reliability hierarchy (own site 1.0, chain 0.6, unmatched site 0.5, dataset text 0.5). `cleaning.is_non_official` knows the aggregators | A status for **every** claim in `silver_facility_evidence`, not only the KB services; `tools/verify_claims.py` |
| 3. Multi-pillar matcher | Nothing for the 10 services without a KB (they get a call-ahead list) | Token / embedding matching on equipment, procedure, specialty and capability claims |
| 4. Ranking, warnings | `navigation/rank.py`: tiers, then confidence band, then distance; warnings for PIN-area positions, shared phones, review reasons | A `SEMANTIC_MATCH` tier |
| 5. UI | Find care: service picker, town/PIN box (no coordinates needed), evidence drawer, warnings | Radius slider, verification filter |
| 6. Tests | 400 tests | Tests for the new parts |

**To settle before building:**
- **"Official" can't just mean `.org` / `.edu.in` / `.gov.in`.** A government or society site often lists many
  hospitals, so it doesn't prove that one facility offers a service. Today only the facility's **own** site counts
  as official.
- **Keep a semantic match out of the verified tiers.** Token overlap at 0.75 matches look-alikes (CT angiography
  vs a cath lab, a dental emergency line vs an ER): the KBs' confusable lists exist for exactly this. A
  `SEMANTIC_MATCH` should rank below LIKELY and say "not verified" in the UI.
- **Don't "boost to 1.0".** A KB CONFIRMED record already carries its own score, and replacing it hides the evidence
  behind it.

---

# Agent Execution Blueprint: NGO Healthcare Service Recommender & Claim Verifier

> **Instructions for the Agent:**  
> Execute the following steps sequentially. Each step contains exact file targets, implementation specifications, and a validation gate. Do not skip validation gates. Run tests at each step to ensure regressions are not introduced.

---

## Mission & Scope
Transition this repository from a patient-facing symptom triage prototype into an **evidence-based clinical service recommendation engine for NGO / Community Health Workers** across India.

### Key Changes:
1. **Target User:** Community / NGO health worker (NOT the patient).
2. **Input:** Structured healthcare service requirement (e.g., Hemodialysis, C-Section, NICU, Trauma Care) + Location (Lat/Lon or PIN) + Distance (up to 150 km).
3. **No Emergency / Symptom Triage:** Remove free-text symptom analysis, red-flag screening, emergency call blocks (112/108), and urgency floors.
4. **Evidence Claim Verification:** Enhance `silver_facility_evidence` by adding `claim_status` and `source_trust_score`, cross-referencing claims against `description` and `source_urls`.
5. **Hybrid 4-Pillar Matching:** For any requested service, match against facilities within **150 km** using:
   - **Path A:** Strict clinical Knowledge Bases (KBs) for high-stakes procedures.
   - **Path B:** Multi-attribute semantic matching comparing the service's expected **equipment, procedure, specialty, and capability** against the facility's exploded claims.
6. **Transparent Evidence Output:** Return composite confidence scores, matched claims, verbatim evidence quotes, and source trustworthiness.

---

## Step 1: Define Service Catalog & Decommission Symptom Modules

### 1.1 Create `configs/services.yaml`
Create a centralized catalog of services supported by the system. Each service must specify its category, clinical KB (if available), and expected 4-pillar requirement profile:

```yaml
version: "1.0.0"
services:
  hemodialysis:
    name: "Hemodialysis"
    category: "Renal Care"
    kb_id: "hemodialysis"
    requirements:
      equipment: ["dialysis machine", "hemodialyzer", "ro water plant"]
      procedures: ["hemodialysis", "haemodialysis", "crrt"]
      specialties: ["nephrology", "pediatricNephrology"]
      capabilities: ["dialysis unit", "renal care"]

  cardiac_catheterization:
    name: "Cardiac Catheterization (Cath Lab / Angioplasty)"
    category: "Cardiology"
    kb_id: "cardiac_catheterization"
    requirements:
      equipment: ["cath lab", "flat-panel fluoroscopy", "biplane"]
      procedures: ["coronary angiography", "angioplasty", "ptca", "pci", "stenting"]
      specialties: ["interventionalCardiology", "cardiology"]
      capabilities: ["24x7 cath lab", "cardiac icu", "ccu"]

  emergency_department:
    name: "24x7 Emergency Department"
    category: "Emergency & Trauma"
    kb_id: "emergency_department"
    requirements:
      equipment: ["resuscitation bay", "defibrillator", "ventilator"]
      procedures: ["emergency resuscitation", "triage", "trauma stabilization"]
      specialties: ["emergencyMedicine", "traumaSurgery"]
      capabilities: ["24x7 emergency", "casualty ward", "ambulance service"]

  ct_scan:
    name: "CT Scan"
    category: "Diagnostic Imaging"
    kb_id: "ct_scan"
    requirements:
      equipment: ["ct scanner", "computed tomography", "multislice ct"]
      procedures: ["ct scan", "contrast ct", "hrct"]
      specialties: ["radiology", "diagnosticRadiology"]
      capabilities: ["ct imaging", "24x7 radiology"]

  emergency_c_section:
    name: "24x7 Emergency C-Section"
    category: "Maternal Health"
    kb_id: null
    requirements:
      equipment: ["operation theatre", "fetal monitor", "anesthesia workstation"]
      procedures: ["cesarean section", "c-section", "emergency delivery"]
      specialties: ["gynecologyAndObstetrics", "obstetricsAndGynecology"]
      capabilities: ["24x7 maternity", "labor room", "obstetric icu"]

  nicu:
    name: "Neonatal Intensive Care Unit (NICU)"
    category: "Maternal & Child Health"
    kb_id: null
    requirements:
      equipment: ["infant radiant warmer", "neonatal incubator", "neonatal ventilator", "phototherapy unit"]
      procedures: ["neonatal resuscitation", "exchange transfusion"]
      specialties: ["neonatologyPerinatalMedicine", "pediatrics"]
      capabilities: ["level iii nicu", "neonatal intensive care"]

  mri:
    name: "MRI"
    category: "Diagnostic Imaging"
    kb_id: null
    requirements:
      equipment: ["mri scanner", "1.5 tesla mri", "3 tesla mri"]
      procedures: ["mri scan", "magnetic resonance imaging"]
      specialties: ["radiology", "diagnosticRadiology"]
      capabilities: ["mri center", "imaging department"]

  orthopedics:
    name: "Orthopedics & Fracture Care"
    category: "Surgical Specialties"
    kb_id: null
    requirements:
      equipment: ["c-arm", "orthopedic surgical set", "traction"]
      procedures: ["fracture fixation", "joint replacement", "arthroscopy"]
      specialties: ["orthopedicSurgery", "orthopedics"]
      capabilities: ["orthopedic department", "trauma ward"]
```

### 1.2 Update Request / Response Contracts (`src/hackathon/navigation/contract.py`)
Modify the contract to reflect the NGO service request model:
- Define `ServiceRecommendRequest`:
  - `service_id: str` (Key from `configs/services.yaml`)
  - `latitude: float` (-90 to 90)
  - `longitude: float` (-180 to 180)
  - `radius_km: float = 150.0` (Default 150 km, max 250 km)
  - `min_confidence: float = 0.5`
  - `max_results: int = 10`
- Define `MatchedClaim`:
  - `pillar: Literal["equipment", "procedure", "specialty", "capability"]`
  - `claim_text: str`
  - `claim_status: str`
  - `source_trust_score: float`
  - `source_url: Optional[str]`
- Define `FacilityServiceRecommendation`:
  - `hospital_id: str`
  - `hospital_name: str`
  - `facility_type: str`
  - `distance_km: float`
  - `overall_confidence: float`
  - `verification_tier: Literal["CONFIRMED", "LIKELY", "SEMANTIC_MATCH", "UNVERIFIED"]`
  - `matched_claims: list[MatchedClaim]`
  - `corroborating_quotes: list[str]`
  - `official_phone: Optional[str]`
  - `warnings: list[str]`

### Validation Gate 1:
```bash
.venv/bin/python -c "
import yaml
from pathlib import Path
from hackathon.navigation.contract import ServiceRecommendRequest

cfg = yaml.safe_load(Path('configs/services.yaml').read_text())
assert 'hemodialysis' in cfg['services']
req = ServiceRecommendRequest(service_id='hemodialysis', latitude=18.52, longitude=73.85, radius_km=150.0)
print('Gate 1 Passed: Contract and services config valid.')
"
```

---

## Step 2: Implement Evidence Claim Verification (`claim_status` & Source Trust)

### 2.1 Domain Trust Rules (`src/hackathon/verification/domain_trust.py`)
Create a domain scoring helper using existing rules in `cleaning.py`:
- Official Domains (`.org`, `.edu.in`, `.gov.in`, or matching facility name): score `1.0`.
- Known Aggregators / Social Directories (Practo, JustDial, Facebook, IndiaMart, Lybrate): score `0.45`.
- Unrecognized generic domains: score `0.60`.
- Shared cross-city domains: score `0.50`.

### 2.2 Claim Verifier (`src/hackathon/verification/claim_verifier.py`)
Create a claim verifier that audits claims from `silver_facility_evidence`:
For each facility claim (in `procedure`, `equipment`, `capability`, `specialty`):
1. **Description Search:** Perform case-insensitive boundary search in `facility.description`:
   - If found with negative context (*"referred"*, *"not available"*, *"outsourced"*, *"discontinued"*): `CONTRADICTED` (Trust: `0.0`).
   - If found in positive context: `CORROBORATED_BY_DESC`.
2. **Official Website Search:** If crawler cache / official site is present, verify claim text presence. If matched on facility's own domain: `VERIFIED_OFFICIAL`.
3. **Copy Detection:** Use `src/hackathon/verification/copies.py` to check if claim appears identically in $\ge 3$ unrelated facilities. If so: `COPIED_LISTING`.
4. **Default:** If present only in directory list: `UNVERIFIED_LISTING`.

### 2.3 Update Evidence Table Pipeline (`notebooks/02b_facility_evidence.py` & `tools/verify_claims.py`)
Add a CLI tool `tools/verify_claims.py` that processes `silver_facility_evidence` (or a local CSV) and generates `silver_facility_evidence_verified.csv` with added columns:
- `claim_status` (`VERIFIED_OFFICIAL`, `CORROBORATED_BY_DESC`, `UNVERIFIED_LISTING`, `COPIED_LISTING`, `CONTRADICTED`)
- `source_trust_score` (float `0.0` to `1.0`)
- `corroboration_quote` (string excerpt)

### Validation Gate 2:
```bash
.venv/bin/python -c "
from hackathon.verification.domain_trust import score_domain
assert score_domain('https://aiims.edu.in') >= 0.9
assert score_domain('https://www.justdial.com') <= 0.5
print('Gate 2 Passed: Domain trust scoring functional.')
"
```

---

## Step 3: Multi-Pillar Semantic Matcher within 150 km

### 3.1 Create `src/hackathon/navigation/matcher.py`
Implement `ServiceMatcher`:
1. **Geospatial Filter (150 km):**
   ```python
   distances = haversine_km(req_lat, req_lon, facilities.latitude, facilities.longitude)
   in_range = (distances <= req.radius_km) & (~np.isnan(distances))
   candidates = facilities[in_range]
   ```
2. **Pillar Extraction:**
   For a candidate facility, retrieve its claims grouped by:
   - `facility_equipment`
   - `facility_procedures`
   - `facility_specialties`
   - `facility_capabilities`
3. **Matching Algorithm:**
   For each requested service requirement pillar ($E, P, S, C$):
   - Check exact synonym / token overlap.
   - Compute semantic embedding similarity (using `databricks-qwen3-embedding-0-6b` if available, or token Jaccard / substring matching with threshold $\ge 0.75$).
   - Weight each match by the claim's `claim_status_weight` and `source_trust_score`:
     - `VERIFIED_OFFICIAL`: `1.0`
     - `CORROBORATED_BY_DESC`: `0.85`
     - `UNVERIFIED_LISTING`: `0.60`
     - `COPIED_LISTING`: `0.30`
     - `CONTRADICTED`: `0.0` (Disqualifies the facility)
4. **Pillar Aggregation:**
   $$\text{Pillar Score} = 0.35 \times S_{\text{procedure}} + 0.30 \times S_{\text{equipment}} + 0.20 \times S_{\text{specialty}} + 0.15 \times S_{\text{capability}}$$
   If a service has a verified Knowledge Base record (e.g. `hemodialysis` with `CONFIRMED` status), overwrite or boost the score to `1.0`.

### Validation Gate 3:
```bash
.venv/bin/python -c "
from hackathon.navigation.matcher import ServiceMatcher
matcher = ServiceMatcher()
res = matcher.match(service_id='hemodialysis', lat=18.5204, lon=73.8567, radius_km=150.0)
assert len(res) > 0
print(f'Gate 3 Passed: Found {len(res)} candidate facilities within 150 km.')
"
```

---

## Step 4: Re-Ranking Engine & Uncertainty Accounting

### 4.1 Update `src/hackathon/navigation/rank.py`
Refactor the ranking logic:
1. **Filter Out Disqualified:**
   - Exclude facilities where any required pillar is `CONTRADICTED`.
   - Exclude facilities with `location_status == "not_found"` or missing coordinates.
2. **Sorting Tuple:**
   Sort candidate facilities by:
   ```python
   sort_key = (
       -tier_priority(facility.verification_tier),  # CONFIRMED (3) > LIKELY (2) > SEMANTIC (1)
       -round(facility.overall_confidence, 2),       # High confidence first
       round(facility.distance_km, 1)               # Proximity ascending (0 -> 150 km)
   )
   ```
3. **Data Quality Warnings:**
   Attach structured warning flags:
   - `coords_approximate`: If location was resolved to median PIN area rather than exact GPS.
   - `unverified_sources_only`: If claims only come from third-party aggregators.
   - `shared_phone`: If official phone is flagged as shared across facilities in multiple cities.

### Validation Gate 4:
```bash
.venv/bin/python -c "
from hackathon.navigation.rank import rank_service_facilities
# Verify sort order: CONFIRMED always outranks unverified matches regardless of distance
"
```

---

## Step 5: Redesign Streamlit NGO User Interface

### 5.1 Refactor `app/pages/1_Find_care.py`
Update the page layout for NGO workers:
1. **Header & Context:**
   - Title: **"Healthcare Service Finder for Community Health Workers"**
   - Subcaption: *"Locate verified facilities and capabilities within 150 km across India."*
2. **Input Form:**
   - `service`: `st.selectbox("Select Required Service", options=list(services.keys()), format_func=lambda s: services[s]['name'])`
   - `c1, c2, c3 = st.columns(3)`:
     - `c1.number_input("Latitude", value=18.5204)`
     - `c2.number_input("Longitude", value=73.8567)`
     - `c3.slider("Radius (km)", min_value=10, max_value=150, value=100, step=10)`
   - `min_conf = st.selectbox("Verification Filter", ["All Matches", "Confirmed & High Confidence Only", "Confirmed Only"])`
3. **Result Cards:**
   For each recommended hospital, display:
   - **Header:** Name, Facility Type, Distance (e.g. `14.2 km`), Confidence Badge (`CONFIRMED 95%`).
   - **Contact:** Direct verified phone number (warning if shared directory number).
   - **Evidence Drawer (`st.expander("Evidence & Matched Claims")`):**
     - Table of matched `Equipment`, `Procedures`, and `Specialties`.
     - Direct quote from website or description.
     - Source URL link and Source Trust Indicator (`Verified Official Site` vs `Directory Listing`).
   - **Data Quality Alerts:** Warnings on location resolution or copied text.

### Validation Gate 5:
```bash
# Launch app in background and verify no syntax or runtime errors on page load
.venv/bin/python -m py_compile app/pages/1_Find_care.py
echo 'Gate 5 Passed: Streamlit UI compiled cleanly.'
```

---

## Step 6: End-to-End Verification & Test Suite

### 6.1 Create `tests/test_ngo_service_recommender.py`
Implement tests covering:
1. **Service Selection:** Querying for `hemodialysis` at Pune coordinates (`18.5204, 73.8567`) returns verified dialysis facilities within 150 km.
2. **Radius Boundary Enforcement:** Facilities at 151 km are strictly excluded; facilities at 149 km are evaluated.
3. **Pillar Verification:** Verifies that a facility without nephrology and dialysis machines is not marked `CONFIRMED`.
4. **Source Trust Penalty:** Ensures an aggregator-only listing receives a lower confidence score than an official hospital site listing.

### Execute Full Test Suite:
```bash
.venv/bin/pytest tests/test_ngo_service_recommender.py -v
```

---

## Summary Checklist for the Agent
- [ ] `configs/services.yaml` created with 4-pillar requirements.
- [ ] `src/hackathon/navigation/contract.py` updated with `ServiceRecommendRequest`.
- [ ] `src/hackathon/verification/claim_verifier.py` created to audit `claim_status` and `source_trust_score`.
- [ ] `src/hackathon/navigation/matcher.py` implemented for 150 km multi-attribute search.
- [ ] `app/pages/1_Find_care.py` refactored into the NGO Service Finder UI.
- [ ] Unit tests in `tests/test_ngo_service_recommender.py` passing.
