"""The normalized profile object every adapter reads from (serialized to user_profile.json)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel):
    city: str = ""
    state: str = ""
    country: str = "United States"
    postal_code: str = ""

    @property
    def short(self) -> str:
        return ", ".join(p for p in (self.city, self.state) if p)


class Personal(BaseModel):
    first_name: str
    last_name: str
    preferred_name: str | None = None
    email: str
    phone: str = ""
    pronouns: str | None = None
    location: Location = Field(default_factory=Location)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Links(BaseModel):
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: dict[str, str] = Field(default_factory=dict)


class Education(BaseModel):
    school: str
    degree: str = ""
    major: str = ""
    minor: str | None = None
    gpa: float | None = None
    gpa_scale: float = 4.0
    start: str | None = None  # YYYY-MM
    graduation: str | None = None  # YYYY-MM
    coursework: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str | None = None
    end: str | None = None
    current: bool = False
    highlights: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    url: str | None = None
    description: str = ""
    technologies: list[str] = Field(default_factory=list)


class WorkAuthorization(BaseModel):
    authorized_to_work_us: bool | None = None
    requires_sponsorship_now: bool | None = None
    requires_sponsorship_future: bool | None = None


class EEOC(BaseModel):
    gender: str = "decline"
    race_ethnicity: str = "decline"
    hispanic_latino: str = "decline"
    veteran_status: str = "decline"
    disability_status: str = "decline"


class Preferences(BaseModel):
    earliest_start_date: str | None = None
    willing_to_relocate: bool | None = None
    salary_expectation: str | None = None


class SavedAnswer(BaseModel):
    pattern: str  # case-insensitive regex matched against the normalized field label
    answer: str


class UserProfile(BaseModel):
    personal: Personal
    links: Links = Field(default_factory=Links)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    eeoc: EEOC = Field(default_factory=EEOC)
    preferences: Preferences = Field(default_factory=Preferences)
    answers: list[SavedAnswer] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @property
    def primary_education(self) -> Education | None:
        return self.education[0] if self.education else None

    @property
    def latest_experience(self) -> Experience | None:
        current = [e for e in self.experience if e.current]
        if current:
            return current[0]
        return max(self.experience, key=lambda e: e.end or e.start or "", default=None)
