from pathlib import Path

from autoapply.config import load_config
from autoapply.jobs.sources import manual_job
from autoapply.profile.answers import load_learned, save_learned
from autoapply.profile.parser import fill_missing, from_resume_text, github_username
from autoapply.store.db import JobStatus, JobStore

ROOT = Path(__file__).resolve().parents[1]


def test_store_lifecycle(tmp_path):
    with JobStore(tmp_path / "t.db") as store:
        job = manual_job("https://jobs.lever.co/acme/1", "acme", "Data Science Intern")
        assert store.upsert(job) is True
        assert store.upsert(job) is False
        store.set_status(job.job_id, JobStatus.QUEUED)
        store.upsert(job)  # refresh must not reset status
        assert store.get(job.job_id).status == JobStatus.QUEUED
        store.set_status(job.job_id, JobStatus.APPLIED, "ok")
        assert store.count_applied_today("acme") == 1
        assert store.get(job.job_id).applied_at
        assert [r["event"] for r in store.logs(job.job_id)] == ["status", "status"]


def test_example_config_loads():
    cfg = load_config(ROOT / "config" / "config.example.yaml")
    assert cfg.automation.require_submit_confirmation is True
    assert cfg.resolve("data/x.db") == (ROOT / "data" / "x.db").resolve()


def test_fill_missing_never_overwrites():
    base = {"personal": {"email": "a@x.com", "phone": ""}, "links": {}}
    fill_missing(base, {"personal": {"email": "b@x.com", "phone": "555"}, "links": {"github": "g"}})
    assert base == {"personal": {"email": "a@x.com", "phone": "555"}, "links": {"github": "g"}}


def test_resume_text_extraction():
    out = from_resume_text("Jane Doe | jane@x.edu | (305) 555-0100 | github.com/janedoe")
    assert out["personal"]["email"] == "jane@x.edu"
    assert out["links"]["github"] == "https://github.com/janedoe"
    assert github_username(out["links"]["github"]) == "janedoe"


def test_learned_answers_roundtrip(tmp_path):
    p = tmp_path / "a.yaml"
    save_learned(p, "Are you open to a hybrid schedule? *", "Yes")
    save_learned(p, "Are you open to a hybrid schedule? *", "No")  # replaces, not duplicates
    [a] = load_learned(p)
    assert a.answer == "No"
    import re

    assert re.search(a.pattern, "Are you open to a hybrid schedule?", re.I)
