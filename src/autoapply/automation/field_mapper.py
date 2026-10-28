"""Map a form field to a value from the profile — or return None so a human decides.

Design rule: a wrong answer on an application is worse than a pause. Every rule
either returns data you supplied, or nothing. Ambiguous option matches return None.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..profile.models import UserProfile
from .fields import FieldDescriptor

DECLINE = "decline"
_DECLINE_HINTS = (
    "decline",
    "not wish",
    "don't wish",
    "do not wish",
    "prefer not",
    "not to answer",
    "not to disclose",
    "not to self",
    "rather not",
    "choose not",
)
_OTHER_COUNTRIES = re.compile(
    r"canada|united kingdom|\buk\b|europe|\beu\b|india|germany|australia|ireland|singapore|mexico",
    re.I,
)
_MONTHS = "January February March April May June July August September October November December"


def yes_no(flag: bool | None) -> str | None:
    return None if flag is None else ("Yes" if flag else "No")


Getter = Callable[[UserProfile, FieldDescriptor], "str | None"]


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    getter: Getter
    exclude: re.Pattern[str] | None = None
    max_label_len: int | None = None


@dataclass(frozen=True)
class Resolution:
    value: str | None
    source: str


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.I)


# ---------------------------------------------------------------- getters


def _edu(attr: str) -> Getter:
    def get(p: UserProfile, f: FieldDescriptor) -> str | None:
        e = p.primary_education
        v = getattr(e, attr, None) if e else None
        return str(v) if v not in (None, "") else None

    return get


def _gpa(p: UserProfile, f: FieldDescriptor) -> str | None:
    e = p.primary_education
    return f"{e.gpa:.2f}".rstrip("0").rstrip(".") if e and e.gpa is not None else None


def _graduation(p: UserProfile, f: FieldDescriptor) -> str | None:
    e = p.primary_education
    if not e or not e.graduation:
        return None
    year, _, month = e.graduation.partition("-")
    label = f.clean_label.lower()
    if f.options and year in f.options:
        return year
    if "year" in label and "month" not in label:
        return year
    if "month" in label and "year" not in label:
        return _MONTHS.split()[int(month) - 1] if month else None
    return f"{month}/{year}" if month else year


def _work_auth(p: UserProfile, f: FieldDescriptor) -> str | None:
    if _OTHER_COUNTRIES.search(f.clean_label):
        return None  # only US authorization is recorded; anything else is a human call
    return yes_no(p.work_authorization.authorized_to_work_us)


def _sponsorship(p: UserProfile, f: FieldDescriptor) -> str | None:
    if _OTHER_COUNTRIES.search(f.clean_label):
        return None
    wa = p.work_authorization
    if "future" in f.key_text:
        if wa.requires_sponsorship_now is None or wa.requires_sponsorship_future is None:
            return None
        return yes_no(wa.requires_sponsorship_now or wa.requires_sponsorship_future)
    return yes_no(wa.requires_sponsorship_now)


def _latest(attr: str) -> Getter:
    def get(p: UserProfile, f: FieldDescriptor) -> str | None:
        e = p.latest_experience
        return getattr(e, attr) if e else None

    return get


def _const(fn: Callable[[UserProfile], str | None]) -> Getter:
    return lambda p, f: fn(p) or None


# Order matters: first matching rule wins, so specific rules come before generic ones.
DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        "preferred_name",
        _rx(r"preferred (first )?name|nickname"),
        _const(lambda p: p.personal.preferred_name or p.personal.first_name),
    ),
    Rule(
        "first_name",
        _rx(r"\bfirst[\s_-]*name|given[\s_-]*name|\bfname\b"),
        _const(lambda p: p.personal.first_name),
    ),
    Rule(
        "last_name",
        _rx(r"\blast[\s_-]*name|surname|family[\s_-]*name|\blname\b"),
        _const(lambda p: p.personal.last_name),
    ),
    Rule("email", _rx(r"e-?mail"), _const(lambda p: p.personal.email), max_label_len=40),
    Rule("phone", _rx(r"phone|mobile|\bcell\b"), _const(lambda p: p.personal.phone), max_label_len=40),
    Rule("linkedin", _rx(r"linkedin"), _const(lambda p: p.links.linkedin), max_label_len=60),
    Rule("github", _rx(r"github"), _const(lambda p: p.links.github), max_label_len=60),
    Rule(
        "portfolio",
        _rx(r"portfolio|personal (web)?site|^website|website url|other website"),
        _const(lambda p: p.links.portfolio),
        max_label_len=60,
    ),
    Rule(
        "full_name",
        _rx(r"\bfull[\s_-]*name|^name\b|^your name|legal name"),
        _const(lambda p: p.personal.full_name),
        exclude=_rx(r"company|school|university|employer|reference|manager|recruiter|referr"),
    ),
    Rule("pronouns", _rx(r"pronoun"), _const(lambda p: p.personal.pronouns), max_label_len=40),
    Rule(
        "postal_code",
        _rx(r"\bzip\b|postal"),
        _const(lambda p: p.personal.location.postal_code),
        max_label_len=40,
    ),
    Rule("city", _rx(r"^city\b|\bcity$"), _const(lambda p: p.personal.location.city), max_label_len=40),
    Rule(
        "state",
        _rx(r"^state\b|state/province|^province"),
        _const(lambda p: p.personal.location.state),
        max_label_len=40,
    ),
    Rule(
        "location",
        _rx(r"^location\b|current location|where are you (currently )?(located|based)"),
        _const(lambda p: p.personal.location.short),
        max_label_len=80,
    ),
    Rule(
        "school",
        _rx(r"\bschool\b|university|college|institution"),
        _edu("school"),
        exclude=_rx(r"high school|attend|graduate school|why"),
        max_label_len=60,
    ),
    Rule(
        "degree",
        _rx(r"\bdegree\b"),
        _edu("degree"),
        exclude=_rx(r"degree of|completed|highest|level"),
        max_label_len=60,
    ),
    Rule(
        "major",
        _rx(r"discipline|\bmajor\b|field of study|area of study|concentration"),
        _edu("major"),
        max_label_len=60,
    ),
    Rule("gpa", _rx(r"\bgpa\b|grade point"), _gpa, max_label_len=60),
    Rule(
        "graduation",
        _rx(r"graduat|expected completion|completion date|class of"),
        _graduation,
        exclude=_rx(r"did you|have you|high school"),
        max_label_len=60,
    ),
    Rule(
        "current_company",
        _rx(r"current (company|employer)|most recent (company|employer)|\borg\b"),
        _latest("company"),
        max_label_len=60,
    ),
    Rule("current_title", _rx(r"current (job )?(title|role|position)"), _latest("title"), max_label_len=60),
    Rule(
        "work_authorization",
        _rx(
            r"authori[sz]ed to work|legally (authori[sz]ed|eligible|able|permitted) to work"
            r"|eligible to work|work authori[sz]ation|right to work"
        ),
        _work_auth,
    ),
    Rule("sponsorship", _rx(r"sponsor|require (a )?visa|visa sponsorship"), _sponsorship),
    Rule(
        "start_date",
        _rx(r"start date|available to start|earliest (start|availability)|when can you start"),
        _const(lambda p: p.preferences.earliest_start_date),
        exclude=_rx(r"month|year|education|employment"),
    ),
    Rule("relocate", _rx(r"relocat"), _const(lambda p: yes_no(p.preferences.willing_to_relocate))),
    Rule(
        "salary",
        _rx(r"salary|compensation|pay (expectation|requirement|range)|desired pay"),
        _const(lambda p: p.preferences.salary_expectation),
    ),
    Rule("hispanic_latino", _rx(r"hispanic|latin[oax]"), _const(lambda p: p.eeoc.hispanic_latino)),
    Rule("race_ethnicity", _rx(r"\brace\b|ethnicit"), _const(lambda p: p.eeoc.race_ethnicity)),
    Rule("veteran", _rx(r"veteran"), _const(lambda p: p.eeoc.veteran_status)),
    Rule("disability", _rx(r"disabilit"), _const(lambda p: p.eeoc.disability_status)),
    Rule("gender", _rx(r"\bgender\b|\bsex\b"), _const(lambda p: p.eeoc.gender), exclude=_rx(r"pronoun")),
)


class FieldMapper:
    def __init__(self, profile: UserProfile, rules: tuple[Rule, ...] = DEFAULT_RULES):
        self.profile = profile
        self.rules = rules

    def resolve(self, f: FieldDescriptor) -> Resolution | None:
        """None = nothing recognizes this field. Resolution(value=None) = recognized, no data."""
        label = f.clean_label
        for ans in self.profile.answers:
            if label and re.search(ans.pattern, label, re.I):
                return Resolution(ans.answer, f"saved answer /{ans.pattern}/")
        if f.kind == "textarea" and len(label) > 40:
            return None  # long free-text prompt = essay question → always human
        key = f.key_text
        for rule in self.rules:
            if rule.max_label_len and len(label) > rule.max_label_len:
                continue
            if not rule.pattern.search(key):
                continue
            if rule.exclude and rule.exclude.search(key):
                continue
            value = rule.getter(self.profile, f)
            return Resolution(value if value not in (None, "") else None, rule.name)
        return None


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def choose_option(options: list[str], value: str) -> str | None:
    """Pick exactly one option for `value`; None if no match or more than one plausible match."""
    if not options or not value:
        return None
    v = _n(value)
    opts = [(o, _n(o)) for o in options]

    def unique(hits: list[str]) -> str | None:
        return hits[0] if len(hits) == 1 else None

    exact = [o for o, n in opts if n == v]
    if exact:
        return unique(exact)
    if v == DECLINE:
        return unique([o for o, n in opts if any(h in n for h in _DECLINE_HINTS)])
    if v in ("yes", "no"):
        return unique([o for o, n in opts if re.match(rf"{v}\b", n)])
    starts = [o for o, n in opts if n.startswith(v) or (len(n) >= 3 and v.startswith(n))]
    if starts:
        return unique(starts)
    return unique([o for o, n in opts if v in n or (len(n) >= 4 and n in v)])
