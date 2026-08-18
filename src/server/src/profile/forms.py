from django import forms
from django.core.exceptions import ValidationError

from core.models import User


MAX_AVATAR_BYTES = 3 * 1024 * 1024
MAX_AVATAR_PIXELS = 20_000_000


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "avatar"]
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "autocomplete": "username",
                    "maxlength": User._meta.get_field("username").max_length,
                }
            ),
            "avatar": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def clean_username(self):
        username = User.normalize_username(self.cleaned_data["username"].strip())
        username_is_taken = (
            User.objects.filter(username__iexact=username)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if username_is_taken:
            raise ValidationError(
                "That username is already in use.",
                code="duplicate_username",
            )
        return username

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        uploaded_avatar = self.files.get(self.add_prefix("avatar"))
        if not avatar or uploaded_avatar is None:
            return avatar

        if uploaded_avatar.size > MAX_AVATAR_BYTES:
            raise ValidationError(
                "Choose an image smaller than 3 MB.",
                code="avatar_too_large",
            )

        image = getattr(avatar, "image", None)
        if image and image.width * image.height > MAX_AVATAR_PIXELS:
            raise ValidationError(
                "Choose an image smaller than 20 megapixels.",
                code="avatar_too_many_pixels",
            )
        return avatar
