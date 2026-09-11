from django.contrib import admin
from django import forms

from core.post_admin import PostLinkAdmin
from explore.models import PublicPost


class PublicPostForm(forms.ModelForm):
    class Meta:
        model = PublicPost
        fields = ["post", "cosound"]


@admin.register(PublicPost)
class PublicPostAdmin(PostLinkAdmin):
    form = PublicPostForm
    autocomplete_fields = ["post", "cosound"]
    fields = ["post", "edit_post", "cosound"]
    list_display = ["post", "cosound"]
