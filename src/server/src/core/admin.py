import secrets

from import_export.admin import ImportExportModelAdmin
from import_export import resources, widgets, fields
from unfold.contrib.import_export.forms import ExportForm, ImportForm
import json
from django.contrib import admin, messages
from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.template.loader import render_to_string
from django_file_form.model_admin import FileFormAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin, TabularInline, StackedInline
from unfold.contrib.filters.admin import FieldTextFilter
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from taggit.models import Tag

from core.models import Manager, User, Sound, Player, Listener, Cosound, Artist, Set
from core.forms import SoundForm


class ListenerInline(StackedInline):
    model = Listener


class ManagerInline(StackedInline):
    model = Manager


class ArtistInline(StackedInline):
    model = Artist
    extra = 0


class SetInline(TabularInline):
    model = Set
    extra = 0


class PlayerInline(TabularInline):
    model = Player
    extra = 0


model = django_apps.get_model("django_file_form", "TemporaryUploadedFile")
admin.site.unregister(model)

admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    inlines = [ManagerInline, ArtistInline, ListenerInline]

    class Meta:
        model = User
        verbose_name = "Users"

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_filter_submit = True
    list_filter = [("email", FieldTextFilter)]


class VectorWidget(widgets.Widget):
    def clean(self, value, row=None, **kwargs):
        if not value:
            return None
        if isinstance(value, list):
            return value
        s = str(value).strip().strip("[]")
        return [float(x) for x in s.split()]

    def render(self, value, obj=None, **kwargs):
        if value is None:
            return ""
        return str(value)


class TaggitWidget(widgets.Widget):
    def render(self, value, obj=None, **kwargs):
        if not value:
            return ""
        if hasattr(value, "names"):
            return ", ".join(value.names())
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)

    def clean(self, value, row=None, **kwargs):
        if not value:
            return []
        return [tag.strip() for tag in value.split(",") if tag.strip()]


class TagsField(fields.Field):
    def save(self, instance, row, is_m2m=False, **kwargs):
        # Tag assignment is deferred to SoundResource.after_save_instance
        # because TaggableManager doesn't support setattr and requires a saved pk.
        return


class ArtistByNameWidget(widgets.ForeignKeyWidget):
    """Resolve an artist by name on import, creating it if it doesn't exist."""

    def clean(self, value, row=None, **kwargs):
        name = (value or "").strip()
        if not name:
            return None
        artist, _ = self.model.objects.get_or_create(name=name)
        return artist

    def render(self, value, obj=None, **kwargs):
        return value.name if value else ""


class SoundResource(resources.ModelResource):
    embeddings = fields.Field(
        column_name="embeddings",
        attribute="embeddings",
        widget=VectorWidget(),
    )
    artist = fields.Field(
        column_name="artist",
        attribute="artist",
        widget=ArtistByNameWidget(Artist, "name"),
    )
    tags = TagsField(
        column_name="tags",
        attribute="tags",
        widget=TaggitWidget(),
    )

    class Meta:
        model = Sound
        import_id_fields = ["id"]
        fields = (
            "id",
            "title",
            "artist",
            "tags",
            "embeddings",
            "flavor",
            "file",
            "art",
        )

    def after_save_instance(self, instance, row, **kwargs):
        raw = row.get("tags") if hasattr(row, "get") else None
        if raw is None:
            return
        if isinstance(raw, (list, tuple)):
            tag_list = [str(t).strip() for t in raw if str(t).strip()]
        else:
            tag_list = [t.strip() for t in str(raw).split(",") if t.strip()]
        if tag_list:
            instance.tags.set(tag_list, clear=True)
        else:
            instance.tags.clear()


@admin.register(Sound)
class SoundAdmin(FileFormAdmin, ModelAdmin, ImportExportModelAdmin):  # type: ignore
    form = SoundForm
    resource_classes = [SoundResource]

    # Add Unfold's styled forms for the import/export pages
    import_form_class = ImportForm
    export_form_class = ExportForm

    list_display = ["title", "artist", "created_at", "updated_at"]
    compressed_fields = True
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "title",
                    "artist",
                    "art",
                    "file",
                ],
            },
        ),
        (
            "Details",
            {
                "fields": [
                    "set",
                    "flavor",
                    "tags",
                ],
            },
        ),
    ]


@admin.register(Player)
class PlayerAdmin(ModelAdmin):
    list_display = ["name", "manager"]
    list_filter = [("name", FieldTextFilter)]
    filter_horizontal = ["sounds"]
    readonly_fields = ["playing_display", "token_display"]
    compressed_fields = True
    fieldsets = [
        (
            "Player Info",
            {
                "fields": [
                    "name",
                    "photo",
                    "bio",
                    "manager",
                    "location",
                ],
            },
        ),
        (
            "Player Sound",
            {
                "fields": [
                    "sounds",
                    "playing_display",
                ],
            },
        ),
    ]

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "sounds":
            kwargs["label"] = "Sound library"
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_form(self, request, obj=None, change=False, **kwargs):
        self._current_request = request
        return super().get_form(request, obj, change, **kwargs)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/regenerate-token/",
                self.admin_site.admin_view(self.regenerate_token_view),
                name="core_player_regenerate_token",
            ),
        ]
        return custom + urls

    def regenerate_token_view(self, request, object_id):
        player = self.get_object(request, object_id)
        if player is None:
            messages.error(request, "Player not found.")
            return HttpResponseRedirect(reverse("admin:core_player_changelist"))
        player.token = secrets.token_hex(32)
        player.save(update_fields=["token"])
        messages.success(request, "Token regenerated.")
        return HttpResponseRedirect(
            reverse("admin:core_player_change", args=[player.pk])
        )

    @admin.display(description="Token")
    def token_display(self, obj):
        if not obj or not obj.token:
            return mark_safe(
                '<div class="text-sm text-font-subtle-light dark:text-font-subtle-dark">—</div>'
            )
        token = obj.token
        refresh_url = reverse("admin:core_player_regenerate_token", args=[obj.pk])
        btn_style = (
            "display:inline-block;padding:8px 16px;margin-bottom:8px;"
            "border-radius:6px;color:#fff;font-weight:600;border:none;"
            "cursor:pointer;align-self:flex-start;"
        )
        return mark_safe(f"""
            <div class="flex flex-col gap-2 w-full">
                <div class="py-6 text-md text-font-light dark:text-font-dark">
                    This Token is used to Identify this Player during API calls.
                </div>
              <input type="text" readonly value="{token}"
                class="font-mono text-xs bg-base-50 dark:bg-base-900 border border-base-200 dark:border-base-800 rounded-default px-2 py-1 w-full min-w-0 text-ellipsis overflow-hidden whitespace-nowrap"
                onclick="this.select()" />
              <div class="flex flex-col w-full">
                <button type="button"
                  style="{btn_style}background:#2563eb;"
                  onclick="navigator.clipboard.writeText('{token}').then(() => {{ const t=this.innerText; this.innerText='✓ Copied'; setTimeout(() => this.innerText=t, 1500); }})">
                  📋 Copy Token
                </button>
                <div class="py-6 text-md text-font-light dark:text-font-dark">
                    The buttons below copy premade commands to run this player using it's token. To run, Download the main repo from
                    <a href="https://github.com/ohjime/cosound" target="_blank" rel="noopener" class="underline">https://github.com/ohjime/cosound</a>,
                    open the folder in a terminal, and paste one of the commands copied below.
                    Use the Remote command if the player is running remotely (production),
                    or the Local command if the player is running locally (development).
                    Use the <strong>Master</strong> slider to set the player's master volume (0–100). The copied
                    command will start the player at that level; the value is converted to a 0.0–1.0 gain.
                </div>
                <div class="flex items-center gap-3 mb-2">
                  <label for="master-slider-{obj.pk}" class="text-sm font-medium text-font-default-light dark:text-font-default-dark">Master</label>
                  <input id="master-slider-{obj.pk}" type="range" min="0" max="100" value="70"
                    class="flex-1"
                    oninput="document.getElementById('master-value-{obj.pk}').innerText = this.value;" />
                  <span id="master-value-{obj.pk}" class="w-10 text-right tabular-nums text-sm text-font-default-light dark:text-font-default-dark">70</span>
                </div>
                <button type="button"
                  style="{btn_style}background:#0f766e;"
                  onclick="(() => {{ const v = (document.getElementById('master-slider-{obj.pk}').value / 100).toFixed(2); const cmd = `make player token={token} master_gain=${{v}}`; navigator.clipboard.writeText(cmd).then(() => {{ const t=this.innerText; this.innerText='✓ Copied'; setTimeout(() => this.innerText=t, 1500); }}); }})()">
                  📋 Copy Local Player Command
                </button>
                <button type="button"
                  style="{btn_style}background:#7c3aed;"
                  onclick="(() => {{ const v = (document.getElementById('master-slider-{obj.pk}').value / 100).toFixed(2); const cmd = `make player run=remote token={token} master_gain=${{v}}`; navigator.clipboard.writeText(cmd).then(() => {{ const t=this.innerText; this.innerText='✓ Copied'; setTimeout(() => this.innerText=t, 1500); }}); }})()">
                  📋 Copy Remote Player Command
                </button>
                
                <button type="button"
                  style="{btn_style}background:#dc2626;"
                  onclick="
                    if (!confirm('Regenerate token? The old token will stop working.')) return;
                    const csrftoken = document.cookie.split('; ').find(r => r.startsWith('csrftoken='))?.split('=')[1];
                    fetch('{refresh_url}', {{
                      method: 'POST',
                      headers: {{ 'X-CSRFToken': csrftoken }},
                      credentials: 'same-origin',
                    }}).then(r => {{
                      if (r.ok || r.redirected) location.reload();
                      else alert('Failed to refresh token');
                    }});">
                  ↻ Refresh Token
                </button>
              </div>
            </div>
            """)

    @admin.display(description="Last Updated Cosound")
    def playing_display(self, obj):
        playing = obj.playing
        layers = []
        if playing is not None:
            data = playing.model_dump() if hasattr(playing, "model_dump") else playing
            layers = (data or {}).get("layers", []) or []

        if not layers:
            return mark_safe(
                '<div class="text-sm text-font-subtle-light dark:text-font-subtle-dark">'
                "Nothing playing"
                "</div>"
            )

        sound_ids = [l.get("sound_id") for l in layers if l.get("sound_id") is not None]
        sounds = {s.pk: s for s in Sound.objects.filter(pk__in=sound_ids)}

        rows = []
        for layer in layers:
            sid = layer.get("sound_id")
            gain = float(layer.get("sound_gain", 0) or 0)
            pct = max(0, min(100, round(gain * 100)))
            sound = sounds.get(sid)
            title = sound.title if sound else "(missing sound)"
            artist = sound.artist_name if sound else ""
            meta = f"{artist} &middot; #{sid}" if artist else f"#{sid}"
            rows.append(f"""
                <div class="flex items-center gap-3 py-2 border-b border-base-200 dark:border-base-800 last:border-0">
                  <div class="flex-1 min-w-0">
                    <div class="font-medium text-font-default-light dark:text-font-default-dark truncate">{title}</div>
                    <div class="text-xs text-font-subtle-light dark:text-font-subtle-dark truncate">{meta}</div>
                  </div>
                  <div class="w-40 shrink-0">
                    <div class="h-2 rounded-full bg-base-200 dark:bg-base-800 overflow-hidden">
                      <div class="h-full bg-primary-600 dark:bg-primary-500" style="width: {pct}%"></div>
                    </div>
                  </div>
                  <div class="w-12 text-right tabular-nums text-xs text-font-default-light dark:text-font-default-dark">{gain:.2f}</div>
                </div>
                """)
        return mark_safe(
            '<div class="rounded-default border border-base-200 dark:border-base-800 px-4 py-2 bg-white dark:bg-base-900">'
            + "".join(rows)
            + "</div>"
        )


@admin.register(Manager)
class ManagerAdmin(ModelAdmin):
    list_display = ["name", "user", "created_at"]
    list_filter = [("name", FieldTextFilter)]
    inlines = [PlayerInline]


@admin.register(Artist)
class ArtistAdmin(ModelAdmin):
    list_display = ["name", "user", "created_at"]
    list_filter = [("name", FieldTextFilter)]
    inlines = [SetInline]
    compressed_fields = True
    fieldsets = [
        (
            None,
            {"fields": ["user", "name", "bio", "url"]},
        ),
        (
            "Media",
            {"fields": ["avatar", "cover"]},
        ),
    ]


@admin.register(Set)
class SetAdmin(ModelAdmin):
    list_display = ["name", "artist", "created_at"]
    list_filter = [("name", FieldTextFilter)]


@admin.register(Listener)
class ListenerAdmin(ModelAdmin):
    list_display = ["user", "created_at"]
    readonly_fields = ["collection_display", "created_at", "updated_at"]
    change_form_outer_after_template = "admin/listener_test_point.html"
    compressed_fields = True
    fieldsets = [
        (
            None,
            {"fields": ["user", "created_at", "updated_at"]},
        ),
        (
            "Collection",
            {"classes": ["tab"], "fields": ["collection_display"]},
        ),
    ]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/set-test-point/",
                self.admin_site.admin_view(self.set_test_point_view),
                name="core_listener_set_test_point",
            ),
        ]
        return custom + urls

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "test_point_tags": Tag.objects.order_by("name"),
                "set_test_point_url": reverse(
                    "admin:core_listener_set_test_point", args=[object_id]
                ),
            }
        )
        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

    def set_test_point_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        listener = self.get_object(request, object_id)
        if listener is None:
            messages.error(request, "Listener not found.")
            return HttpResponseRedirect(reverse("admin:core_listener_changelist"))
        if not self.has_change_permission(request, listener):
            raise PermissionDenied

        redirect_url = reverse("admin:core_listener_change", args=[listener.pk])
        tag_id = request.POST.get("test_point_tag")
        try:
            tag = Tag.objects.filter(pk=tag_id).first() if tag_id else None
        except (TypeError, ValueError):
            tag = None
        if tag is None:
            messages.error(request, "Select a valid tag before setting a test point.")
            return HttpResponseRedirect(redirect_url)

        sound_ids = list(
            Sound.objects.filter(tags=tag).values_list("pk", flat=True).distinct()
        )
        with transaction.atomic():
            listener.collection.clear()
            if sound_ids:
                listener.collection.add(*sound_ids)

        if sound_ids:
            sound_label = "sound" if len(sound_ids) == 1 else "sounds"
            messages.success(
                request,
                f'Set test point to "{tag.name}" and replaced the collection '
                f"with {len(sound_ids)} {sound_label}.",
            )
        else:
            messages.warning(
                request,
                f'Set test point to "{tag.name}". No sounds use this tag, '
                "so the collection is now empty.",
            )
        return HttpResponseRedirect(redirect_url)

    @admin.display(description="Saved sounds")
    def collection_display(self, obj):
        sounds = list(obj.collection.all().order_by("title")) if obj else []
        if not sounds:
            return mark_safe(
                '<div class="text-sm text-font-subtle-light dark:text-font-subtle-dark">'
                "Empty collection"
                "</div>"
            )
        rows = []
        for s in sounds:
            url = reverse("admin:core_sound_change", args=[s.pk])
            rows.append(
                f'<li class="py-2 border-b border-base-200 dark:border-base-800 last:border-0">'
                f'<a href="{url}" class="font-medium text-font-default-light dark:text-font-default-dark hover:underline">{s.title}</a>'
                f'<span class="text-xs text-font-subtle-light dark:text-font-subtle-dark"> &middot; {s.artist_name} &middot; #{s.pk}</span>'
                f"</li>"
            )
        return mark_safe(
            '<ul class="rounded-default border border-base-200 dark:border-base-800 px-4 bg-white dark:bg-base-900 list-none m-0">'
            + "".join(rows)
            + "</ul>"
        )


@admin.register(Cosound)
class CosoundAdmin(ModelAdmin):
    list_display = ["created_at"]
    # Present so other admins can autocomplete a cosound (explore posts pick
    # one this way). `=id` is an exact match rather than the default icontains:
    # a cosound has no name to search, and LIKE against an integer primary key
    # is an error on Postgres. The hashid is the other handle a person has on
    # a specific mix, and it is a text column, so it takes the usual match.
    search_fields = ["=id", "hashid"]
