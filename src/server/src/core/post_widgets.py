import json
from itertools import zip_longest

from django import forms


class EasyMDEWidget(forms.Textarea):
    """Markdown editor for the admin, backed by vendored EasyMDE.

    Only meant for parent-form fields (easymde_init.js runs once on page
    load) — don't put it on admin inlines.
    """

    def __init__(self, attrs=None):
        super().__init__({"data-easymde": "true", **(attrs or {})})

    class Media:
        css = {"all": ["core/vendor/easymde.min.css"]}
        js = ["core/vendor/easymde.min.js", "core/js/easymde_init.js"]


class AuthorsWidget(forms.Widget):
    """Edit the embedded author objects without exposing their JSON."""

    template_name = "admin/core/authors_widget.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        authors = self._authors_from_value(value)
        context["widget"]["authors"] = authors or [{}]
        return context

    def value_from_datadict(self, data, files, name):
        getlist = getattr(data, "getlist", None)

        def values_for(suffix):
            key = f"{name}_{suffix}"
            if getlist:
                return getlist(key)
            value = data.get(key, [])
            return value if isinstance(value, list) else [value]

        authors = []
        fields = zip_longest(
            values_for("name"),
            values_for("role"),
            values_for("url"),
            fillvalue="",
        )
        for author_name, role, url in fields:
            author = {
                "name": author_name.strip(),
                "role": role.strip(),
                "url": url.strip(),
            }
            if any(author.values()):
                authors.append(author)
        return json.dumps(authors)

    def value_omitted_from_data(self, data, files, name):
        return f"{name}_present" not in data

    @staticmethod
    def _authors_from_value(value):
        if not value:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return []
        if not isinstance(value, list):
            return []
        return [author for author in value if isinstance(author, dict)]

    class Media:
        css = {"all": ["core/css/authors_widget.css"]}
        js = ["core/js/authors_widget.js"]
