from django import forms

from explore.models import Comment


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
