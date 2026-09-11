from django_file_form.model_admin import FileFormAdmin
from django_file_form.forms import FileFormMixin, UploadedFileField
from django import forms
from django.forms import ModelForm
from taggit.models import Tag
from unfold.widgets import UnfoldAdminSelect2MultipleWidget
from core.widgets import AudioUploadWidget
from core.models import Comment, LocalPost, Post, Sound, User
from core.fonts import article_font_choices
from core.post_widgets import AuthorsWidget, EasyMDEWidget


class SoundForm(FileFormMixin, ModelForm):
    file = UploadedFileField(widget=AudioUploadWidget)
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=UnfoldAdminSelect2MultipleWidget,
    )

    class Meta:
        model = Sound
        fields = ["file", "title", "artist", "set", "art", "flavor", "tags"]

    readonly_fields = ["timestamp"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, s3_upload_dir="sounds", **kwargs)
        if self.instance.pk:
            self.fields["tags"].initial = self.instance.tags.all()

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.tags.set(self.cleaned_data.get("tags", []))


class UserAvatarForm(FileFormMixin, ModelForm):
    avatar = UploadedFileField(required=False)

    class Meta:
        model = User
        fields = ["avatar"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, s3_upload_dir="avatars", **kwargs)


class PostForm(forms.ModelForm):
    font_family = forms.ChoiceField(
        label="Post font",
        required=False,
        help_text="Used for selected elements in the article layout.",
    )
    authors = forms.JSONField(
        required=False,
        initial=list,
        help_text=(
            "Add each author and their role. The URL is optional and can go "
            "straight to the author's own page — their site, a profile, a "
            "label — rather than anything hosted here. The credit opens it in "
            "a new tab, so the post keeps playing behind it."
        ),
        widget=AuthorsWidget,
    )

    class Meta:
        model = Post
        fields = "__all__"
        widgets = {"article": EasyMDEWidget()}
        help_texts = {
            "article": (
                "Markdown. External links open in a new tab. In Explore, link "
                "to a featured layer using [the rain](#layer-2) or "
                "[the rain](#layer-rain-on-tin). Layer links select that layer "
                "in the fixed mix; links without a matching layer render as text."
            ),
        }

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


class LocalPostForm(forms.ModelForm):
    class Meta:
        model = LocalPost
        fields = ["post", "collection"]


class CommentForm(forms.ModelForm):
    body = forms.CharField(
        max_length=2000,
        error_messages={"required": "Write a comment before posting."},
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-bordered h-28 w-full resize-y",
                "maxlength": 2000,
                "placeholder": "Share what you heard, noticed, or felt…",
                "required": True,
                "aria-label": "Your comment",
            }
        ),
    )

    class Meta:
        model = Comment
        fields = ["body"]

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("Write a comment before posting.")
        return body
