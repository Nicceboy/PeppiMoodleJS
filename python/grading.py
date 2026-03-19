# USAGE: uv run --with "requests,beautifulsoup4" grading.py
# Modify the constants below as needed
import csv
import hashlib
import os
import tempfile
import time
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# Grab from the cookies after logging in in Peppi
SHIBBOLETH = {"_shibsession_<hex>": "_<hex>"}
JSESSIONID = "<uppercase hex>"

# Liferay Portal CSRF token, should be changed!
# Either read the root index.html from the JS <script> snippet that has this (Liferay.authToken = ) , or capture some traffic
LIFERAY_CSRF = "iOkBKQ0q"

# Default date to be assigned for completing the course
REPORTING_DATE = "08.03.2026"
# Course identifier, usually the course code
COURSE_ID = "IC00AI82-3004"

# Semicolon separated text export from Moodle
MOODLE_CSV_FILENAME = "moodle_grades.csv"

CACHE_TTL = 15 * 60  # 15 minutes in seconds

# From Peppi HTML
GRADE_MAPPING = {
    "1": "138",
    "2": "139",
    "3": "140",
    "4": "141",
    "5": "142",
    "HYV/PASS": "144",
    "HYL/FAIL": "143",
    "KHY/PASSD": "45586",
    "HT/GOOD": "92988",
    "TT/FAIR": "92989",
}

BASE = (
    "https://suunnittelu.peppi.oulu.fi/group/opettajan-tyopoyta/toteutuksen-arviointi"
)
PORTLET = "AssessmentManagementPortlet_WAR_assessmentmanagementportlet"
PREFIX = f"_{PORTLET}"


def build_url(action, extra_params=None, lifecycle=2):
    params = {
        "p_p_id": PORTLET,
        "p_p_lifecycle": lifecycle,
        "p_p_state": "normal",
        "p_p_mode": "view",
        "p_p_cacheability": "cacheLevelPage",
        f"{PREFIX}_struts.portlet.action": f"/assessment/{action}",
        f"{PREFIX}_struts.portlet.mode": "view",
        f"{PREFIX}_pager.resultsPerPage": 500,
        f"{PREFIX}_pager.currentPage": 1,
        f"{PREFIX}_realizationCode": COURSE_ID,
    }
    if extra_params:
        params.update(extra_params)
    return f"{BASE}?{urlencode(params)}"


URLS = {
    # --- Core flow ---
    "table": build_url("assessment", {f"{PREFIX}_view": "fragment"}, lifecycle=0),
    # Used to set the initial grade
    "commit_accomplishment": build_url("commit_accomplishment"),
    "is_entitlement_absent": build_url("is_entitlement_absent"),
    # --- Student management ---
    "add_student": build_url("add_student"),
    "check_study_right_dates": build_url("check_study_right_dates"),
    "check_pending": build_url("check_pending_accomplishments"),
    "check_enrollment": build_url("check_enrollment"),
    # --- Accomplishment management ---
    "clear_assessment": build_url("clear_assessment"),
    "is_modify_allowed": build_url("is_accomplishment_modify_allowed"),
    "delete_row": build_url("delete_accomplishment"),
    "update_grade": build_url("update_grade"),
    "check_grade": build_url("check_grade"),
    # --- Parts / retries ---
    "add_part": build_url("add_part"),
    "edit_part": build_url("edit_part"),
    "add_retry": build_url("add_retry"),
    # --- Modals / display ---
    "history": build_url("accomplishment_history"),
    "sub_attainments": build_url("sub_attainments"),
    "edit_field": build_url("edit_field"),
    "edit_course_unit": build_url("edit_course_unit_form"),
    "col_toggle": build_url("toggle_column"),
}


def parse_moodle_csv(filepath):
    """
    Parse a Moodle grades CSV (semicolon-delimited), English as language.
    Note that has custom field "Total points".

    Returns a list of dicts with keys:
        first_name, last_name, id_number, email, points, course_total

    Raises ValueError if a student has 0 total points but a course total > 1.
    """
    students = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            raw_points = row["Total points (Real)"]
            points = 0.0 if raw_points.strip() == "-" else float(raw_points)
            course_total = row["Course total (Real)"]
            course_total = (
                0.0
                if course_total.strip() == "-"
                else int(float(row["Course total (Real)"]))
            )
            name = f"{row['First name']} {row['Last name']}"
            if points == 0 and course_total > 1:
                raise ValueError(
                    f"Student {name} has 0 points but course total is {course_total}"
                )
            students.append(
                {
                    "first_name": row["First name"],
                    "last_name": row["Last name"],
                    "id_number": row["ID number"],
                    "email": row["Email address"],
                    "points": points,
                    "course_total": course_total,
                }
            )
    return students


def match_moodle_to_peppi(csv_path, peppi_students):
    """
    Load the Moodle CSV, then match each student to a Peppi table entry.

    - Skips students whose course_total == 0.
    - Matches by surname first. If exactly one Peppi entry shares the surname,
      that is used. If multiple share the surname, first name is also checked.
    - Raises ValueError for any CSV student that cannot be matched or is ambiguous.

    Returns a list of dicts:
        { 'peppi': <peppi student dict>, 'moodle': <moodle student dict> }
    """
    moodle_students = parse_moodle_csv(csv_path)
    results = []

    for moodle in moodle_students:
        if moodle["course_total"] == 0:
            continue

        first = moodle["first_name"].lower()
        last = moodle["last_name"].lower()
        full = f"{moodle['first_name']} {moodle['last_name']}"

        surname_matches = [p for p in peppi_students if last in p["name"].lower()]

        if len(surname_matches) == 1:
            match = surname_matches[0]
        elif len(surname_matches) > 1:
            first_matches = [p for p in surname_matches if first in p["name"].lower()]
            if len(first_matches) == 1:
                match = first_matches[0]
            elif len(first_matches) == 0:
                raise ValueError(
                    f"Surname matched multiple Peppi entries but first name '{first}' "
                    f"matched none for: {full}"
                )
            else:
                raise ValueError(
                    f"Ambiguous match — multiple Peppi entries match full name for: {full}"
                )
        else:
            raise ValueError(f"No Peppi match found for: {full}")

        results.append({"peppi": match, "moodle": moodle})

    return results


def parse_table(html):
    soup = BeautifulSoup(html, "html.parser")
    students = []
    for tr in soup.find("table", {"id": "evaluation"}).find_all(
        "tr", attrs={"data-id": True}
    ):
        entitlement_id = tr["data-entitlement-id"]
        student_right_number = tr["data-entitlement-key"]
        accomplishment_id = tr["data-accomplishment-id"]
        grade = tr.find("select", {"name": "accomplishment.grade.id"})
        selected = grade.find("option", selected=True)
        students.append(
            {
                "entitlement_id": entitlement_id,
                "accomplishment_id": accomplishment_id,
                "grade": selected["value"] if selected else None,
                "name": tr["data-student-name"],
                "student_right_number": student_right_number,
            }
        )
    return students


def build_accomplishment_payload(
    study_entitlement_id,
    credits,
    grade_id,
    reporting_date="",
    default_reporting_date="",
    accomplishment_id="",
    p_auth="",
):
    return {
        "": [study_entitlement_id],
        "accomplishment.credits": str(credits),
        "accomplishment.grade.id": str(grade_id),
        "accomplishment.reportingDate": reporting_date,
        "accomplishment.studyEntitlementId": str(study_entitlement_id),
        "accomplishment.id": accomplishment_id,
        "defaultReportingDate": default_reporting_date,
        "p_auth": p_auth,
    }


def encode_accomplishment_payload(payload):
    """
    Prepare the accomplishment payload for application/x-www-form-urlencoded.

    Handles repeated keys (empty string keys) properly.
    """
    pairs = []
    for key, value in payload.items():
        if isinstance(value, list):
            for v in value:
                pairs.append((key, v))
        else:
            pairs.append((key, value))
    return pairs


def _cache_path(url):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return os.path.join(tempfile.gettempdir(), f"peppi_table_{url_hash}.html")


# Fetches the assesment table from Peppi, caching the HTML. Student table is parsed.
def fetch_table(session):
    cache_file = _cache_path(URLS["table"])

    if os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < CACHE_TTL:
            print(f"[cache] Using cached response ({int(age)}s old)")
            with open(cache_file, "r", encoding="utf-8") as f:
                html = f.read()
            students = parse_table(html)
            return students

    response = session.get(URLS["table"])
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(response.text)

    students = parse_table(response.text)
    return students


if __name__ == "__main__":
    session = requests.Session()
    session.cookies.update(
        {
            "JSESSIONID": JSESSIONID,
            **SHIBBOLETH,
        }
    )
    session.headers.update(
        {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }
    )
    peppi_students = fetch_table(session)
    # TRY ONE FIRST, then adjust the list in the end
    for student in match_moodle_to_peppi(MOODLE_CSV_FILENAME, peppi_students)[:1]:
        name = student.get("peppi").get("name")
        entitlement_id = student.get("peppi").get("entitlement_id")
        grade = student.get("moodle").get("course_total")
        grade_id = GRADE_MAPPING.get(str(grade))
        print(f"Processing {name} with ID {entitlement_id} and grade {grade}")
        payload = build_accomplishment_payload(
            entitlement_id, 5, grade_id, "", REPORTING_DATE, "", LIFERAY_CSRF
        )
        encoded_pairs = encode_accomplishment_payload(payload)
        default_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = session.post(
            URLS["commit_accomplishment"], data=encoded_pairs, headers=default_headers
        )
        try:
            resp_json = resp.json()
            if isinstance(resp_json, dict) and resp_json.get("error"):
                print(f"[ERROR] {resp_json.get('message', 'Unknown error')}")
                print(f"  Data: {resp_json.get('data', '')}")
            else:
                print("Grade succesfully added.")
        except (ValueError, KeyError):
            print(resp.text)
