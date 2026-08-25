from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline, StackedInline

from core.models import Listener
from library.models import SoundMix


@admin.register(SoundMix)
class SoundMixAdmin(ModelAdmin):
    list_display = [
        "creator",
        "title",
        "cosound",
        "created_at",
    ]


CurrentListenerAdmin = type(admin.site._registry[Listener])
admin.site.unregister(Listener)


@admin.register(Listener)
class ListenerAdmin(CurrentListenerAdmin):
    readonly_fields = tuple(getattr(CurrentListenerAdmin, "readonly_fields", ())) + (
        "mixes_display",
    )
    fieldsets = list(CurrentListenerAdmin.fieldsets) + [
        (
            "Mixes",
            {"classes": ["tab"], "fields": ["mixes_display"]},
        ),
    ]

    @admin.display(description="Mixes")
    def mixes_display(self, obj):
        if not obj:
            return ""
        mixes = list(
            SoundMix.objects.filter(creator=obj.user)
            .select_related("cosound")
            .order_by("-created_at")
        )
        if not mixes:
            return mark_safe(
                '<div class="text-sm text-font-subtle-light dark:text-font-subtle-dark">'
                "No mixes"
                "</div>"
            )
        rows = []
        for m in mixes:
            url = reverse("admin:library_soundmix_change", args=[m.pk])
            title = m.title or f"Mix #{m.pk}"
            ts = m.created_at.strftime("%Y-%m-%d %H:%M")
            rows.append(
                f'<li class="py-2 border-b border-base-200 dark:border-base-800 last:border-0 flex items-center gap-3">'
                f'<a href="{url}" class="font-medium text-font-default-light dark:text-font-default-dark hover:underline">{title}</a>'
                f'<span class="text-xs text-font-subtle-light dark:text-font-subtle-dark ml-auto">{ts}</span>'
                f"</li>"
            )
        return mark_safe(
            '<ul class="rounded-default border border-base-200 dark:border-base-800 px-4 bg-white dark:bg-base-900 list-none m-0">'
            + "".join(rows)
            + "</ul>"
        )
