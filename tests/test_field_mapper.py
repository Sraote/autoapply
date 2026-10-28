import pytest

from autoapply.automation.field_mapper import FieldMapper, choose_option
from autoapply.automation.fields import FieldDescriptor


def fd(label, tag="input", input_type="text", options=None, name="", **kw):
    return FieldDescriptor(
        idx=0,
        tag=tag,
        input_type=input_type,
        label=label,
        name=name,
        options=options or [],
        option_values=options or [],
        **kw,
    )


@pytest.mark.parametrize(
    "label,expected",
    [
        ("First Name *", "Jane"),
        ("Last Name *", "Doe"),
        ("Full name", "Jane Doe"),
        ("Email", "jane.doe@example.com"),
        ("LinkedIn Profile", "https://www.linkedin.com/in/janedoe"),
        ("GitHub URL", "https://github.com/janedoe"),
        ("School", "University of Example"),
        ("Discipline", "Data Science"),
        ("GPA", "3.7"),
        ("Expected graduation date", "05/2027"),
        ("Graduation year", "2027"),
        ("Current company", "Example Labs"),
        ("How did you hear about this job?", "Company careers page"),
    ],
)
def test_text_rules(profile, label, expected):
    res = FieldMapper(profile).resolve(fd(label))
    assert res is not None and res.value == expected


def test_company_name_is_not_full_name(profile):
    res = FieldMapper(profile).resolve(fd("Company name"))
    assert res is None or res.value != "Jane Doe"


def test_work_authorization_and_sponsorship(profile):
    m = FieldMapper(profile)
    opts = ["Yes", "No"]
    auth = m.resolve(
        fd("Are you legally authorized to work in the United States?", tag="select", options=opts)
    )
    assert auth.value == "Yes"
    sp = m.resolve(fd("Will you now or in the future require visa sponsorship?", tag="select", options=opts))
    assert sp.value == "No"


def test_other_country_authorization_goes_to_human(profile):
    res = FieldMapper(profile).resolve(fd("Are you authorized to work in Canada?"))
    assert res is not None and res.value is None


def test_salary_is_never_guessed(profile):
    res = FieldMapper(profile).resolve(fd("What are your salary expectations?"))
    assert res is not None and res.value is None


def test_essay_goes_to_human(profile):
    res = FieldMapper(profile).resolve(
        fd(
            "Why do you want to work at our company, and what excites you about this role?",
            tag="textarea",
            input_type="textarea",
        )
    )
    assert res is None


def test_lever_name_field(profile):
    res = FieldMapper(profile).resolve(fd("Full name✱", name="name"))
    assert res.value == "Jane Doe"


def test_choose_option_decline():
    opts = ["Male", "Female", "Non-binary", "I don't wish to answer"]
    assert choose_option(opts, "decline") == "I don't wish to answer"


def test_choose_option_yes_variants():
    assert choose_option(["Yes, I am authorized", "No"], "Yes") == "Yes, I am authorized"


def test_choose_option_ambiguous_returns_none():
    opts = ["Data Science (BS)", "Data Science (BA)"]
    assert choose_option(opts, "Data Science") is None


def test_choose_option_location_prefix():
    opts = ["Miami, Florida, United States", "Miami Beach, Florida, United States"]
    assert choose_option(opts, "Miami, FL") == "Miami, Florida, United States"


def test_choose_option_no_match():
    assert choose_option(["Red", "Blue"], "Green") is None
