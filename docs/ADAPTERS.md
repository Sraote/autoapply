# Writing an ATS adapter

Most of the work (field collection, mapping, prompts, submit review) is shared, so a new adapter
is usually 30–60 lines. Start by copying `adapters/lever.py`.

```python
# src/autoapply/automation/adapters/ashby.py
import re
from .base import ATSAdapter

class AshbyAdapter(ATSAdapter):
    portal_type = "ashby"                  # must match jobs/sources.py detect_portal()
    url_patterns = ("ashbyhq.com",)
    form_selector = "form"                 # narrows field collection to the application form
    submit_selector = "button[type=submit]"

    async def open(self):
        url = self.job.url
        if not url.rstrip("/").endswith("/application"):
            url = url.rstrip("/") + "/application"
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()

    async def verify_submitted(self) -> bool:
        body = await self.page.locator("body").inner_text()
        return bool(re.search(r"application (was )?(submitted|received)|thanks for applying", body, re.I))
```

Register it in `adapters/__init__.py`:

```python
ADAPTERS = (GreenhouseAdapter, LeverAdapter, AshbyAdapter)
```

## Hooks you can override

| Method | Default | Override when |
|---|---|---|
| `open()` | goto `job.url` | URL needs rewriting, iframes, cookie walls |
| `prepare_form()` | wait for `form_selector` | an "Apply" button or login wall precedes the form |
| `collect_fields()` | generic DOM walk | custom widgets the generic walker can't see |
| `fill_combobox()` | react-select style | the portal uses a different dropdown widget |
| `submit()` | click `submit_selector` | multi-step flows ("Next" pages) |
| `verify_submitted()` | `False` (asks you) | always — teach it the confirmation page |

### Multi-page portals (Workday)

Override `run()` to loop: `fill_form()` → click "Next" → `self._seen.clear()` → repeat until the
review page, then call the base submit/verify logic. Workday also requires an account per tenant:
let the user sign in during a `hitl.pause()` rather than storing credentials.

## Testing

Save an offline copy of the portal's form markup under `tests/fixtures/` (strip personal data and
scripts you don't need) and add a test modeled on `tests/test_e2e_lever.py`: it runs the adapter in
real Chromium with a scripted human, and asserts exactly which questions reached the human. That
assertion is the important one — a new adapter should never answer something it shouldn't.
