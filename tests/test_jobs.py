from datetime import UTC, datetime, timedelta

from autoapply.automation.challenges import classify_frame_src, classify_title
from autoapply.config import SearchConfig
from autoapply.jobs.filters import filter_postings, location_matches, title_matches
from autoapply.jobs.sources import GreenhouseSource, LeverSource, Posting, detect_portal

INC = ["software engineer intern", "data science intern", "new grad"]
EXC = ["senior", "sr", "staff"]


def test_title_filters():
    assert title_matches("Software Engineer Intern, Summer 2027", INC, EXC)
    assert title_matches("Data Science Intern (Fall)", INC, EXC)
    assert not title_matches("Senior Software Engineer Intern Mentor", INC, EXC)
    assert not title_matches("Sr. Data Scientist", INC, EXC)
    assert not title_matches("Product Manager", INC, EXC)


def test_location_filters():
    assert location_matches("Remote - US", ["Miami"], allow_remote=True)
    assert not location_matches("Remote - US", ["Miami"], allow_remote=False)
    assert location_matches("Miami, FL", ["Miami"], allow_remote=False)
    assert location_matches("Anywhere", [], allow_remote=True)


def test_detect_portal():
    assert detect_portal("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse"
    assert detect_portal("https://jobs.lever.co/acme/abc") == "lever"
    assert detect_portal("https://acme.wd5.myworkdayjobs.com/x") == "workday"
    assert detect_portal("https://jobs.ashbyhq.com/acme/1") == "ashby"
    assert detect_portal("https://example.com/careers") == "unknown"


def test_greenhouse_parse():
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Data Science Intern",
                "location": {"name": "Miami, FL"},
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "updated_at": "2026-10-20T10:00:00-04:00",
            }
        ]
    }
    [p] = GreenhouseSource("acme").parse(payload)
    assert p.job_id == "greenhouse:acme:1" and p.location == "Miami, FL" and p.posted_at


def test_lever_parse():
    payload = [
        {
            "id": "abc",
            "text": "Software Engineer Intern",
            "categories": {"location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "createdAt": 1761000000000,
        }
    ]
    [p] = LeverSource("acme").parse(payload)
    assert p.url.endswith("/abc/apply") and p.portal_type == "lever"


def test_filter_postings_recency():
    now = datetime.now(UTC)
    mk = lambda age: Posting(
        "1", "acme", "Data Science Intern", "Remote", "u", "lever", "s", now - timedelta(days=age)
    )
    cfg = SearchConfig(include_titles=INC, exclude_titles=EXC, max_posting_age_days=30)
    assert len(filter_postings([mk(5), mk(60)], cfg)) == 1


def test_challenge_classification():
    assert classify_frame_src("https://newassets.hcaptcha.com/captcha/v1/x") == "hCaptcha"
    assert classify_frame_src("https://www.google.com/recaptcha/api2/anchor?k=x&size=invisible") is None
    assert classify_frame_src("https://www.google.com/recaptcha/api2/bframe?k=x") == "reCAPTCHA"
    assert classify_frame_src("https://challenges.cloudflare.com/cdn-cgi/x") == "Cloudflare Turnstile"
    assert classify_title("Just a moment...") == "Cloudflare check"
    assert classify_title("Apply — Acme") is None
