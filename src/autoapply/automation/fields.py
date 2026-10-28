"""Generic form introspection: find every input, resolve its human-readable label."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..utils import normalize_label

if TYPE_CHECKING:
    from playwright.async_api import Page


@dataclass
class FieldDescriptor:
    idx: int
    tag: str
    input_type: str
    name: str = ""
    id: str = ""
    label: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)
    option_values: list[str] = field(default_factory=list)
    value: str = ""
    role: str = ""
    aria_autocomplete: str = ""

    @property
    def selector(self) -> str:
        return f'[data-autoapply-idx="{self.idx}"]'

    @property
    def kind(self) -> str:
        if self.input_type in ("file", "radio", "checkbox"):
            return self.input_type
        if self.tag == "select":
            return "select"
        if self.tag == "textarea":
            return "textarea"
        if self.role == "combobox" or self.aria_autocomplete in ("list", "both"):
            return "combobox"
        return "text"

    @property
    def clean_label(self) -> str:
        return normalize_label(self.label)

    @property
    def key_text(self) -> str:
        """Label first (rules may anchor on it), then name/id for extra signal."""
        return " ".join(p for p in (self.clean_label, self.name, self.id) if p).lower()

    @classmethod
    def from_dict(cls, d: dict) -> FieldDescriptor:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Tags each control with data-autoapply-idx so Playwright can target it later,
# and resolves labels the way a screen reader would (aria → <label> → container text).
COLLECT_JS = r"""
(rootSelector) => {
  const root = (rootSelector && document.querySelector(rootSelector)) || document;
  const text = (el) => (el ? (el.innerText || el.textContent || "") : "").replace(/\s+/g, " ").trim();
  const labelFor = (el) => {
    const parts = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach(id => parts.push(text(document.getElementById(id))));
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) parts.push(text(l));
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(text(wrap));
    const aria = el.getAttribute("aria-label");
    if (aria) parts.push(aria.trim());
    if (!parts.some(Boolean)) {
      const box = el.closest("fieldset, .field, .application-question, [class*='question'], [class*='field']");
      const l = box && box.querySelector("legend, label, .application-label, [class*='label']");
      if (l) parts.push(text(l));
    }
    if (!parts.some(Boolean) && el.placeholder) parts.push(el.placeholder);
    return [...new Set(parts.filter(Boolean))].join(" | ").slice(0, 500);
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const out = [];
  const seenRadio = new Set();
  let idx = 0;
  for (const el of root.querySelectorAll("input, select, textarea")) {
    const type = (el.tagName === "INPUT" ? (el.getAttribute("type") || "text") : el.tagName).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "search"].includes(type)) continue;
    if (type !== "file" && !visible(el)) continue;
    if (el.disabled) continue;

    if (type === "radio") {
      if (!el.name || seenRadio.has(el.name)) continue;
      seenRadio.add(el.name);
      const group = [...root.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
      const fs = el.closest("fieldset");
      const label = (fs && text(fs.querySelector("legend"))) || labelFor(el.closest("[role=radiogroup]") || el);
      group.forEach((r, j) => r.setAttribute("data-autoapply-radio", `${idx}-${j}`));
      el.setAttribute("data-autoapply-idx", idx);
      const checked = group.find(r => r.checked);
      out.push({idx, tag: "input", input_type: "radio", name: el.name, id: el.id || "", label,
        required: group.some(r => r.required) || /[*✱]/.test(label),
        options: group.map(r => labelFor(r) || r.value), option_values: group.map(r => r.value),
        value: checked ? checked.value : "", role: "", aria_autocomplete: ""});
      idx++;
      continue;
    }

    el.setAttribute("data-autoapply-idx", idx);
    const label = labelFor(el);
    let options = [], option_values = [];
    if (el.tagName === "SELECT") {
      for (const o of el.options) {
        const t = o.text.trim();
        if (!t || (o.value === "" && /^(select|choose|please|--)/i.test(t))) continue;
        options.push(t); option_values.push(o.value);
      }
    }
    let value = el.value || "";
    if (type === "file") value = el.files && el.files.length ? "file" : "";
    if (type === "checkbox") value = el.checked ? "checked" : "";
    out.push({idx, tag: el.tagName.toLowerCase(), input_type: type, name: el.name || "", id: el.id || "",
      label, required: el.required || el.getAttribute("aria-required") === "true" || /[*✱]\s*$/.test(label),
      options, option_values, value, role: el.getAttribute("role") || "",
      aria_autocomplete: el.getAttribute("aria-autocomplete") || ""});
    idx++;
  }
  return out;
}
"""


async def collect_fields(page: Page, root_selector: str | None = None) -> list[FieldDescriptor]:
    raw = await page.evaluate(COLLECT_JS, root_selector)
    return [FieldDescriptor.from_dict(d) for d in raw]
