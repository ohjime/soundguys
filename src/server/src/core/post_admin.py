from django.contrib import admin, messages
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin

from core.forms import PostForm


class PostAdmin(ModelAdmin):
    """Shared writing, credits, typography and publication controls."""

    form = PostForm
    compressed_fields = True
    autocomplete_fields = ["composer"]
    content_fields = [
        "announcers_call",
        "title",
        "greeting_style",
        "font_family",
        "article",
        "authors",
        "composer",
    ]
    list_display = ["title", "composer", "publication_state"]
    list_filter = [("publication_date", admin.EmptyFieldListFilter)]
    search_fields = ["title"]
    readonly_fields = ["publication_control"]

    def get_fieldsets(self, request, obj=None):
        return [
            (None, {"fields": self.content_fields}),
            ("Publishing", {"fields": ["publication_control"]}),
        ]

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

    def publication_error(self, obj):
        """Post specializations can require additional data before publishing."""
        return None

    def save_model(self, request, obj, form, change):
        if "_publish" in request.POST and not obj.publication_date:
            error = self.publication_error(obj)
            if error:
                self.message_user(request, error, messages.ERROR)
            else:
                obj.publication_date = timezone.now()
                self.message_user(request, "Post published.", messages.SUCCESS)
        super().save_model(request, obj, form, change)


class PostLinkAdmin(ModelAdmin):
    """Select shared writing without changing the playback source."""

    autocomplete_fields = ["post"]
    search_fields = ["post__title"]
    list_select_related = ["post"]
    readonly_fields = ["edit_post"]

    @admin.display(description="Shared writing")
    def edit_post(self, obj):
        if not obj or not obj.post_id:
            return "Choose a Post, then save to edit its writing."
        from django.urls import reverse
        return format_html('<a href="{}">Edit {}</a>', reverse("admin:core_post_change", args=[obj.post_id]), obj.post.title)
