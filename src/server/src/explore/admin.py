from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin

from explore.fonts import article_font_choices
from explore.models import Comment, Post
from explore.widgets import AuthorsWidget, EasyMDEWidget


class PostForm(forms.ModelForm):
    font_family = forms.ChoiceField(
        label="Post font",
        required=False,
        help_text=(
            "The template applies this font only to elements with the "
            "explore-font class."
        ),
    )
    authors = forms.JSONField(
        required=False,
        initial=list,
        help_text="Add each author and their role. The URL is optional.",
        widget=AuthorsWidget,
    )

    class Meta:
        model = Post
        fields = "__all__"
        widgets = {"article": EasyMDEWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = article_font_choices()
        selected_font = (
            self.data.get("font_family")
            if self.is_bound
            else getattr(self.instance, "font_family", "")
        )
        if selected_font and selected_font not in dict(choices):
            choices.append(
                (
                    selected_font,
                    f"{selected_font} (missing — using Tailwind Sans)",
                )
            )
        self.fields["font_family"].choices = choices

    def clean_authors(self):
        return self.cleaned_data.get("authors") or []


@admin.register(Post)
class PostAdmin(ModelAdmin):
    form = PostForm
    compressed_fields = True
    # A cosound has no name, so the picker is a search box over ids and
    # hashids rather than a select of every mix ever saved.
    autocomplete_fields = ["cosound", "composer"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "announcers_call",
                    "title",
                    "cosound",
                    "greeting_style",
                    "font_family",
                    "article",
                    "authors",
                    "composer",
                ]
            },
        ),
        (
            "Publishing",
            {"fields": ["publication_control"]},
        ),
    ]
    list_display = [
        "title",
        "cosound",
        "composer",
        "publication_state",
    ]
    list_filter = [("publication_date", admin.EmptyFieldListFilter)]
    search_fields = ["title"]
    readonly_fields = ["publication_control"]

    @admin.display(description="Publication")
    def publication_control(self, obj):
        if obj and obj.publication_date:
            return format_html(
                '<span class="font-semibold text-green-600 dark:text-green-400">Published on {}</span>',
                date_format(obj.publication_date, "DATETIME_FORMAT"),
            )
        return mark_safe(
            '<button type="submit" name="_publish" value="1" '
            'class="bg-primary-600 border border-transparent font-medium px-4 py-2 '
            'rounded-default text-sm text-white shadow-xs hover:bg-primary-700 '
            'focus:outline-2 focus:outline-offset-2 focus:outline-primary-600 '
            'dark:bg-primary-500 dark:hover:bg-primary-600">Publish?</button>'
        )

    @admin.display(description="Publication", ordering="publication_date")
    def publication_state(self, obj):
        if obj.publication_date:
            return f"Published on {date_format(obj.publication_date, 'DATE_FORMAT')}"
        return "Draft"

    def save_model(self, request, obj, form, change):
        if "_publish" in request.POST and not obj.publication_date:
            if obj.cosound_id:
                obj.publication_date = timezone.now()
                self.message_user(request, "Post published.", messages.SUCCESS)
            else:
                self.message_user(
                    request,
                    "A Featured Cosound is required before publishing.",
                    messages.ERROR,
                )
        super().save_model(request, obj, form, change)


@admin.register(Comment)
class CommentAdmin(ModelAdmin):
    list_display = ["user", "post", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__username", "post__title", "body"]
    readonly_fields = ["post", "user", "body", "created_at"]
