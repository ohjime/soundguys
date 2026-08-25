import json
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from core.models import Cosound, Listener, Sound
from explore.fonts import article_font_choices, article_font_css_stack
from explore.models import Comment, Post
from explore.renderer import render_markdown


def make_sound(title, **fields):
    """A Sound with its embedding supplied, so save() skips the classifier."""
    return Sound.objects.create(
        file=f"sounds/{title.lower().replace(' ', '-')}.wav",
        title=title,
        embeddings=[0, 0, 0, 0, 0],
        **fields,
    )


def make_cosound(*titles, gain=0.5):
    """A stored mix over freshly created sounds, one layer per title."""
    sounds = [make_sound(title) for title in titles]
    return Cosound.get_or_create_from_layers([(s.pk, gain) for s in sounds])


def make_composer(username="post-composer"):
    composer, _ = get_user_model().objects.get_or_create(
        email=f"{username}@example.com",
        defaults={"username": username},
    )
    return composer


def make_post(**fields):
    # Slugs are generated UUIDs; descriptive legacy slugs in older fixtures are
    # intentionally ignored so each post exercises the real model default.
    fields.pop("slug", None)
    fields.setdefault("composer", make_composer())
    return Post.objects.create(**fields)


class RenderMarkdownTests(SimpleTestCase):
    def test_images_are_stripped(self):
        html = render_markdown("![Landscape](https://example.com/landscape.jpg)")

        self.assertNotIn("<img", html)
        self.assertNotIn("landscape.jpg", html)

    def test_youtube_shortcode_renders_component_after_sanitization(self):
        html = render_markdown('[[youtube id="abc123" caption="A caption"]]')

        self.assertIn("youtube-nocookie.com/embed/abc123", html)
        self.assertIn("<iframe", html)
        self.assertIn("figcaption", html)
        self.assertIn("A caption", html)
        self.assertNotIn("@@sc-", html)

    def test_unknown_shortcode_renders_as_literal_text(self):
        html = render_markdown('[[carousel id="abc"]]')

        self.assertIn("[[carousel", html)

    def test_disallowed_shortcode_attr_is_dropped(self):
        html = render_markdown('[[youtube id="abc" onclick="evil()"]]')

        self.assertIn("youtube-nocookie.com/embed/abc", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("evil()", html)

    def test_script_and_event_handlers_are_stripped(self):
        html = render_markdown('<script>alert(1)</script>')

        self.assertNotIn("<script", html)
        self.assertNotIn("alert(1)", html)

    def test_literal_placeholder_lookalike_is_not_replaced(self):
        html = render_markdown("@@sc-deadbeef-0@@")

        self.assertIn("@@sc-deadbeef-0@@", html)
        self.assertNotIn("<iframe", html)

    def test_markdown_basics_render(self):
        html = render_markdown("## Heading\n\n- one\n- two")

        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn("<li>one</li>", html)


class ExploreViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.published = make_post(
            title="Published Post",
            slug="published-post",
            article="Hello **world**",
            cosound=make_cosound("Rain on tin"),
            publication_date=timezone.now(),
        )
        cls.draft = make_post(
            title="Draft Post", slug="draft-post"
        )

    def test_index_page_renders_shell_without_posts(self):
        response = self.client.get(reverse("explore:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "content-loader")
        self.assertNotContains(response, "Published Post")

    def test_index_fragment_displays_only_published_posts(self):
        response = self.client.get(
            reverse("explore:index"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "Published Post")
        self.assertContains(response, "Hello <strong>world</strong>")
        self.assertNotContains(response, "Draft Post")
        self.assertContains(response, "Click anywhere to start")

    def test_detail_fragment_renders_body_html(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.published.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Hello <strong>world</strong>")

    def test_detail_fragment_keeps_description_above_the_bottom_tabs(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.published.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Comments")
        self.assertContains(response, "Previous Posts")
        self.assertContains(response, "Sign in to join the discussion")
        content = response.content.decode()
        self.assertLess(content.index("Hello <strong>world</strong>"), content.index("Previous Posts"))

    def test_comments_tab_fetches_a_fresh_discussion(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.published.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(
            response,
            f'hx-get="{reverse("explore:discussion", kwargs={"slug": self.published.slug})}"',
        )
        self.assertContains(
            response,
            f'hx-target="#explore-discussion-{self.published.pk}"',
        )

    def test_detail_page_renders_shell(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.published.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "content-loader")

    def test_unpublished_and_unknown_slugs_404(self):
        for slug in [self.draft.slug, uuid.uuid4()]:
            response = self.client.get(
                reverse("explore:detail", kwargs={"slug": slug}),
                HTTP_HX_REQUEST="true",
            )
            self.assertEqual(response.status_code, 404)

    def test_get_absolute_url_uses_main_urlconf(self):
        self.assertEqual(
            self.published.get_absolute_url(), f"/explore/{self.published.slug}/"
        )


class ExploreCommentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.post = make_post(
            title="Discussed Post",
            slug="discussed-post",
            article="Listen closely.",
            cosound=make_cosound("Discussion layer"),
            publication_date=timezone.now(),
        )
        cls.user = get_user_model().objects.create_user(
            username="listener",
            email="listener-comments@example.com",
            password="pw",
        )

    def comment_url(self):
        return reverse("explore:create_comment", kwargs={"slug": self.post.slug})

    def discussion_url(self):
        return reverse("explore:discussion", kwargs={"slug": self.post.slug})

    def test_database_allows_only_one_comment_per_user_and_post(self):
        Comment.objects.create(post=self.post, user=self.user, body="First")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Comment.objects.create(post=self.post, user=self.user, body="Second")

    def test_comment_is_saved_against_the_signed_in_user_and_post(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.comment_url(),
            {"body": "  A thoughtful response.  "},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        comment = Comment.objects.get()
        self.assertEqual(comment.post, self.post)
        self.assertEqual(comment.user, self.user)
        self.assertEqual(comment.body, "A thoughtful response.")
        self.assertContains(response, "You’ve already commented on this post")
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, ">1</span>", html=False)

    def test_signed_out_submission_is_forbidden(self):
        response = self.client.post(
            self.comment_url(),
            {"body": "No identity"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Comment.objects.exists())

    def test_second_submission_does_not_change_the_first_comment(self):
        Comment.objects.create(post=self.post, user=self.user, body="Keep this")
        self.client.force_login(self.user)

        response = self.client.post(
            self.comment_url(),
            {"body": "Replace it"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(Comment.objects.get().body, "Keep this")
        self.assertContains(response, "already commented")
        self.assertNotContains(response, "Replace it")

    def test_blank_comment_returns_the_form_error(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.comment_url(),
            {"body": "   \n"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Write a comment before posting.")
        self.assertFalse(Comment.objects.exists())

    def test_signed_in_form_shows_commenting_identity_and_no_edit_action(self):
        self.client.force_login(self.user)

        response = self.client.get(self.discussion_url())

        self.assertContains(response, "Commenting as")
        self.assertContains(response, self.user.username)
        self.assertContains(response, escape(self.user.avatar_url))
        self.assertContains(response, 'textarea name="body"')
        self.assertContains(response, "No comments yet")
        self.assertNotContains(response, "Edit comment")

    def test_nested_article_renders_the_textarea_and_existing_comments(self):
        Comment.objects.create(
            post=self.post,
            user=self.user,
            body="Visible from another browser",
        )
        other_user = get_user_model().objects.create_user(
            username="other-listener",
            email="other-listener@example.com",
        )
        self.client.force_login(other_user)

        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.post.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'textarea name="body"')
        self.assertContains(response, "Visible from another browser")

    def test_comment_body_is_escaped(self):
        Comment.objects.create(
            post=self.post,
            user=self.user,
            body='<script>alert("no")</script>',
        )

        response = self.client.get(self.discussion_url())

        self.assertNotContains(response, '<script>alert("no")</script>')
        self.assertContains(response, "&lt;script&gt;", html=False)

    def test_comments_are_paginated_newest_first(self):
        for index in range(11):
            user = get_user_model().objects.create_user(
                username=f"listener-{index}",
                email=f"listener-{index}@example.com",
            )
            Comment.objects.create(
                post=self.post,
                user=user,
                body=f"Comment number {index}",
            )

        first_page = self.client.get(self.discussion_url())
        second_page = self.client.get(self.discussion_url(), {"page": 2})

        self.assertContains(first_page, "Comment number 10")
        self.assertNotContains(first_page, "Comment number 0")
        self.assertContains(first_page, "?page=2")
        self.assertContains(second_page, "Comment number 0")
        self.assertNotContains(second_page, "Comment number 10")

    def test_comments_for_a_draft_are_not_public(self):
        draft = make_post(title="Draft", slug="comment-draft")

        response = self.client.get(
            reverse("explore:discussion", kwargs={"slug": draft.slug})
        )

        self.assertEqual(response.status_code, 404)


class ExploreOrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.older = make_post(
            title="Older Post",
            slug="older-post",
            article="Older body text",
            cosound=make_cosound("Older layer"),
            publication_date=now - timedelta(days=1),
        )
        cls.latest = make_post(
            title="Latest Post",
            slug="latest-post",
            article="Latest **body** text",
            cosound=make_cosound("Latest layer"),
            publication_date=now,
        )

    def test_latest_post_renders_in_full_with_previous_posts_in_the_tab(self):
        response = self.client.get(
            reverse("explore:index"), HTTP_HX_REQUEST="true"
        )
        content = response.content.decode()

        self.assertContains(response, "Latest <strong>body</strong> text")
        self.assertContains(response, "Previous Posts")
        self.assertNotContains(response, "Past posts")
        self.assertContains(response, self.older.get_absolute_url())
        self.assertNotContains(
            response, f'<a href="{self.latest.get_absolute_url()}"'
        )
        self.assertIn("Older Post", content)
        self.assertLess(content.index("Latest Post"), content.index("Older Post"))

    def test_unpublished_newer_post_is_ignored(self):
        make_post(
            title="Newer Draft",
            slug="newer-draft",
            article="Draft body text",
        )

        response = self.client.get(
            reverse("explore:index"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "Latest <strong>body</strong> text")
        self.assertNotContains(response, "Newer Draft")


class ExplorePreviousPostsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.current = make_post(
            title="Current Feature",
            slug="current-feature",
            article="Current body",
            cosound=make_cosound("Current feature layer"),
            publication_date=now,
        )
        cls.previous = []
        for index in range(1, 8):
            cls.previous.append(
                make_post(
                    title=f"Previous {index}",
                    slug=f"previous-{index}",
                    cosound=make_cosound(f"Previous layer {index}"),
                    publication_date=now - timedelta(days=index),
                )
            )

        for index in range(2):
            commenter = get_user_model().objects.create_user(
                username=f"previous-commenter-{index}",
                email=f"previous-commenter-{index}@example.com",
            )
            Comment.objects.create(
                post=cls.previous[0],
                user=commenter,
                body=f"Previous comment {index}",
            )

    def test_five_previous_posts_and_one_locked_preview_render(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.current.slug}),
            HTTP_HX_REQUEST="true",
        )

        for index in range(1, 6):
            self.assertContains(response, f"Previous {index}")
            self.assertContains(
                response,
                f'href="{self.previous[index - 1].get_absolute_url()}"',
            )
        self.assertContains(response, "2 comments")
        self.assertContains(response, "Previous 6")
        self.assertContains(response, "data-locked-previous-post")
        self.assertContains(response, "More previous posts are locked")
        self.assertNotContains(
            response, f'href="{self.previous[5].get_absolute_url()}"'
        )
        self.assertNotContains(response, "Previous 7")

    def test_lock_preview_is_absent_when_five_or_fewer_previous_posts_exist(self):
        Post.objects.filter(pk__in=[post.pk for post in self.previous[5:]]).update(
            publication_date=None
        )

        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.current.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertNotContains(response, "data-locked-previous-post")


class ExploreAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.admin_user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="pw"
        )
        cls.post = make_post(
            title="Admin Post",
            slug="admin-post",
            cosound=make_cosound("Admin layer"),
        )

    def test_change_page_mounts_easymde(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:explore_post_change", args=[self.post.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-easymde")
        self.assertContains(response, "explore/vendor/easymde.min.js")

    def test_content_fields_follow_the_requested_admin_order(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:explore_post_change", args=[self.post.pk])
        )
        content = response.content.decode()

        field_ids = [
            "id_announcers_call",
            "id_title",
            "id_cosound",
            "id_greeting_style",
            "id_font_family",
            "id_article",
            "id_authors",
            "id_composer",
        ]
        positions = [content.index(field_id) for field_id in field_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "Featured cosound")
        self.assertContains(response, "Dancing Script")
        self.assertContains(response, "Newsreader")
        self.assertContains(response, "Tailwind Sans")

    def test_authors_are_rendered_as_repeatable_inputs_instead_of_raw_json(self):
        self.post.authors = [
            {
                "name": "Alex Example",
                "role": "Writer",
                "url": "https://example.com/alex",
            }
        ]
        self.post.save(update_fields=["authors"])
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:explore_post_change", args=[self.post.pk])
        )

        self.assertContains(response, 'data-authors-widget="true"')
        self.assertContains(response, 'name="authors_name"')
        self.assertContains(response, 'name="authors_role"')
        self.assertContains(response, 'name="authors_url"')
        self.assertContains(response, 'value="Alex Example"')
        self.assertContains(response, 'aria-label="Add author"')
        self.assertContains(response, 'aria-label="Remove author"')
        self.assertNotContains(response, '[{&quot;name&quot;')

    def test_repeatable_author_inputs_are_saved_as_json_objects(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:explore_post_change", args=[self.post.pk]),
            {
                "announcers_call": "Presenting",
                "title": self.post.title,
                "cosound": str(self.post.cosound_id),
                "greeting_style": "Dear Listener",
                "font_family": "dancing-script",
                "article": "",
                "authors_present": "1",
                "authors_name": ["Alex Example", "Sam Example"],
                "authors_role": ["Writer", "Editor"],
                "authors_url": ["https://example.com/alex", ""],
                "composer": str(self.post.composer_id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(
            self.post.authors,
            [
                {
                    "name": "Alex Example",
                    "role": "Writer",
                    "url": "https://example.com/alex",
                },
                {"name": "Sam Example", "role": "Editor", "url": ""},
            ],
        )

    def test_removing_every_author_saves_an_empty_list(self):
        self.post.authors = [{"name": "Alex", "role": "Writer", "url": ""}]
        self.post.save(update_fields=["authors"])
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:explore_post_change", args=[self.post.pk]),
            {
                "announcers_call": "Presenting",
                "title": self.post.title,
                "cosound": str(self.post.cosound_id),
                "greeting_style": "Dear Listener",
                "font_family": "dancing-script",
                "article": "",
                "authors_present": "1",
                "composer": str(self.post.composer_id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.authors, [])

    def test_draft_page_has_publish_button_and_no_legacy_publication_fields(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:explore_post_change", args=[self.post.pk])
        )

        self.assertContains(response, "Publish?")
        self.assertNotContains(response, 'id="id_slug"')
        self.assertNotContains(response, 'id="id_excerpt"')
        self.assertNotContains(response, 'id="id_is_published"')
        self.assertNotContains(response, 'id="id_publication_date"')
        self.assertNotContains(response, 'id="id_farewell_style"')

    def test_missing_saved_font_remains_editable_with_sans_fallback(self):
        self.post.font_family = "removed-font"
        self.post.save(update_fields=["font_family"])
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:explore_post_change", args=[self.post.pk])
        )

        self.assertContains(response, "removed-font (missing — using Tailwind Sans)")

    def test_publish_button_sets_date_and_becomes_success_text(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:explore_post_change", args=[self.post.pk]),
            {
                "announcers_call": "Presenting",
                "title": self.post.title,
                "cosound": str(self.post.cosound_id),
                "greeting_style": "Dear Listener",
                "font_family": "dancing-script",
                "article": "",
                "authors": "[]",
                "composer": str(self.post.composer_id),
                "_publish": "1",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.post.refresh_from_db()
        self.assertIsNotNone(self.post.publication_date)
        self.assertContains(response, "Published on")
        self.assertNotContains(response, ">Publish?</button>", html=False)


class ExplorePostMetadataTests(TestCase):
    def test_post_defaults_match_the_requested_styles(self):
        post = Post(title="Defaults")

        self.assertEqual(post.announcers_call, "Presenting")
        self.assertEqual(post.greeting_style, "Dear Listener")
        self.assertEqual(post.font_family, "dancing-script")
        self.assertEqual(post.font_css_stack, '"Dancing Script", cursive')
        self.assertEqual(post.authors, [])
        self.assertIsInstance(post.slug, uuid.UUID)
        self.assertFalse(Post._meta.get_field("slug").editable)
        self.assertIsNone(post.composer_id)
        with self.assertRaises(ValidationError) as raised:
            post.full_clean()
        self.assertIn("composer", raised.exception.message_dict)

    def test_local_font_manifests_become_choices_with_a_sans_fallback(self):
        choices = dict(article_font_choices())

        self.assertEqual(choices[""], "Tailwind Sans")
        expected_fonts = {
            "caveat": "Caveat",
            "courgette": "Courgette",
            "dancing-script": "Dancing Script",
            "jacquarda-bastarda-9": "Jacquarda Bastarda 9",
            "marcellus": "Marcellus",
            "merienda": "Merienda",
            "newsreader": "Newsreader",
            "nothing-you-could-do": "Nothing You Could Do",
            "parisienne": "Parisienne",
            "poiret-one": "Poiret One",
            "questrial": "Questrial",
            "reenie-beanie": "Reenie Beanie",
            "sofia": "Sofia",
        }
        for identifier, label in expected_fonts.items():
            with self.subTest(identifier=identifier):
                self.assertEqual(choices[identifier], label)
        self.assertEqual(article_font_css_stack("missing-font"), "var(--font-sans)")

    def test_authors_are_embedded_credits_with_optional_urls(self):
        post = Post(
            title="Credits",
            composer=make_composer("credits-composer"),
            authors=[
                {"name": "Alex", "role": "Writer"},
                {
                    "name": "Sam",
                    "role": "Editor",
                    "url": "https://example.com/sam",
                },
            ],
        )

        post.full_clean()

    def test_invalid_embedded_author_is_rejected(self):
        invalid_authors = [
            {"role": "Writer"},
            {"name": "Alex", "role": ""},
            {"name": "Alex", "role": "Writer", "url": "not-a-url"},
            {"name": "Alex", "role": "Writer", "unknown": "value"},
        ]

        for author in invalid_authors:
            with self.subTest(author=author), self.assertRaises(ValidationError):
                Post(
                    title="Invalid credit",
                    composer=make_composer("invalid-credit-composer"),
                    authors=[author],
                ).full_clean()

    def test_composer_is_a_user_relationship(self):
        composer = get_user_model().objects.create_user(
            username="composer",
            email="composer@example.com",
        )
        post = make_post(
            title="Composed",
            slug="composed",
            composer=composer,
        )

        self.assertEqual(post.composer, composer)
        self.assertEqual(list(composer.composed_explore_posts.all()), [post])

        with self.assertRaises(ProtectedError):
            composer.delete()
        post.refresh_from_db()
        self.assertEqual(post.composer, composer)

    def test_article_renders_call_greeting_and_authors(self):
        post = make_post(
            announcers_call="Now sharing",
            title="A credited post",
            slug="a-credited-post",
            greeting_style="Friends and listeners,",
            font_family="newsreader",
            article="The **article** itself.",
            authors=[
                {
                    "name": "Alex Example",
                    "role": "Writer",
                    "url": "https://example.com/alex",
                }
            ],
            cosound=make_cosound("Credited layer"),
            publication_date=timezone.now(),
        )

        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": post.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Now sharing")
        self.assertContains(response, "Friends and listeners,")
        self.assertContains(response, "The <strong>article</strong> itself.")
        self.assertContains(response, "Alex Example")
        self.assertContains(response, "Writer")
        self.assertContains(response, 'href="https://example.com/alex"')
        self.assertContains(response, escape('"Newsreader", serif'))
        self.assertContains(response, 'class="explore-font text-xl"')


class HomeIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.post = make_post(
            title="Home Tab Post",
            slug="home-tab-post",
            article="Full home **body**",
            cosound=make_cosound("Home layer"),
            publication_date=timezone.now(),
        )

    def test_home_initial_includes_explore_posts(self):
        response = self.client.get(
            reverse("app:home_initial"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "Home Tab Post")

    def test_home_initial_renders_explore_post_in_full(self):
        response = self.client.get(
            reverse("app:home_initial"), HTTP_HX_REQUEST="true"
        )

        self.assertContains(response, "Full home <strong>body</strong>")


class ExploreCosoundRuleTests(TestCase):
    """An Explore post is publishable only while it has a mix behind it."""

    def test_clean_refuses_to_publish_without_a_cosound(self):
        post = Post(
            title="No Mix",
            composer=make_composer("no-mix-composer"),
            publication_date=timezone.now(),
        )

        with self.assertRaises(ValidationError) as raised:
            post.clean()

        self.assertEqual(
            set(raised.exception.message_dict), {"publication_date", "cosound"}
        )

    def test_clean_passes_once_a_cosound_is_attached(self):
        post = Post(
            title="Has Mix",
            composer=make_composer("has-mix-composer"),
            cosound=make_cosound("Clean layer"),
            publication_date=timezone.now(),
        )

        post.clean()

    def test_saving_without_a_cosound_unpublishes(self):
        post = make_post(
            title="Bypassed", publication_date=timezone.now()
        )

        self.assertIsNone(post.publication_date)
        self.assertIsNone(Post.objects.get(pk=post.pk).publication_date)

    def test_clearing_the_cosound_unpublishes_on_save(self):
        post = make_post(
            title="Losing Mix",
            slug="losing-mix",
            cosound=make_cosound("Doomed layer"),
            publication_date=timezone.now(),
        )
        self.assertIsNotNone(post.publication_date)

        post.cosound = None
        post.save()

        self.assertIsNone(Post.objects.get(pk=post.pk).publication_date)

    def test_update_fields_save_still_persists_the_unpublish(self):
        post = make_post(
            title="Narrow Save",
            slug="narrow-save",
            cosound=make_cosound("Narrow layer"),
            publication_date=timezone.now(),
        )

        post.cosound = None
        post.save(update_fields=["cosound"])

        self.assertIsNone(Post.objects.get(pk=post.pk).publication_date)

    def test_deleting_the_cosound_unpublishes_the_post(self):
        cosound = make_cosound("Deleted layer")
        post = make_post(
            title="Orphaned",
            cosound=cosound,
            publication_date=timezone.now(),
        )

        cosound.delete()

        post.refresh_from_db()
        self.assertIsNone(post.cosound)
        self.assertIsNone(post.publication_date)

    def test_published_posts_exclude_a_post_with_no_cosound(self):
        from explore.renderer import get_published_posts

        kept = make_post(
            title="Kept",
            cosound=make_cosound("Kept layer"),
            publication_date=timezone.now(),
        )
        Post.objects.filter(pk=kept.pk).update(cosound=None)

        self.assertEqual(list(get_published_posts()), [])


class CosoundLayerSerializationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sound = make_sound("Tape Hiss", flavor="Warm and grainy.")
        cls.sound.tags.add("ambient", "tape")
        cls.cosound = Cosound.get_or_create_from_layers([(cls.sound.pk, 0.5)])
        cls.post = make_post(
            title="Serialized",
            slug="serialized",
            cosound=cls.cosound,
            publication_date=timezone.now(),
        )

    def test_layers_carry_everything_the_card_reads(self):
        (layer,) = self.post.cosound_sounds()

        self.assertEqual(layer["sound_id"], self.sound.pk)
        self.assertEqual(layer["sound_title"], "Tape Hiss")
        self.assertEqual(layer["sound_gain"], 0.5)
        self.assertEqual(layer["flavor"], "Warm and grainy.")
        self.assertIn("tape", layer["tags"])
        self.assertFalse(layer["mute"])
        self.assertFalse(layer["saved"])

    def test_layers_are_json_serializable(self):
        json.dumps(self.post.cosound_sounds())

    def test_a_collected_sound_opens_with_a_filled_heart(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="collector", email="collector@example.com", password="pw"
        )
        listener = Listener.objects.create(user=user)
        listener.collection.add(self.sound)

        (layer,) = self.post.cosound_sounds(user)

        self.assertTrue(layer["saved"])

    def test_a_post_with_no_cosound_has_no_layers(self):
        draft = make_post(title="Draft", slug="draft-nomix")

        self.assertEqual(draft.cosound_sounds(), [])


class ExploreCardRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sound = make_sound("Harbour Bell")
        cls.post = make_post(
            title="Bells at Dawn",
            slug="bells-at-dawn",
            article="Body text",
            cosound=Cosound.get_or_create_from_layers([(cls.sound.pk, 0.5)]),
            publication_date=timezone.now(),
        )

    def assert_card_is_mounted(self, response):
        # The store handoff, the transport, and a mix that cannot grow.
        self.assertContains(response, 'id="soundLayers"')
        self.assertContains(response, "data-cosound-mixer-root")
        self.assertContains(response, "allowAdd: false")
        self.assertContains(response, 'id="library-master"')
        self.assertContains(response, "Harbour Bell")

    def test_detail_fragment_renders_the_card_above_the_article(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.post.slug}),
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()

        self.assert_card_is_mounted(response)
        self.assertLess(content.index("soundLayers"), content.index("Body text"))

    def test_index_fragment_renders_the_card_for_the_explore_post(self):
        response = self.client.get(reverse("explore:index"), HTTP_HX_REQUEST="true")

        self.assert_card_is_mounted(response)

    def test_save_button_carries_the_post_title(self):
        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.post.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'title: "Bells at Dawn"')

    def test_a_quote_in_the_title_cannot_break_out_of_the_save_payload(self):
        Post.objects.filter(pk=self.post.pk).update(
            title='He said "go" \' now'
        )

        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.post.slug}),
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()

        self.assertIn("\\u0022go\\u0022", content)
        self.assertNotIn('title: "He said "', content)

    def test_a_post_that_lost_its_cosound_is_not_readable(self):
        Post.objects.filter(pk=self.post.pk).update(cosound=None)

        response = self.client.get(
            reverse("explore:detail", kwargs={"slug": self.post.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)


class SaveMixTitleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.user = get_user_model().objects.create_user(
            username="listener", email="listener@example.com", password="pw"
        )
        cls.sound = make_sound("Kept Layer")
        cls.layers = json.dumps([{"sound_id": cls.sound.pk, "sound_gain": 0.5}])

    def setUp(self):
        self.client.force_login(self.user)

    def post_save(self, **extra):
        return self.client.post(
            reverse("library:save"),
            {"layers": self.layers, **extra},
            HTTP_HX_REQUEST="true",
        )

    def test_a_suggested_title_prefills_the_dialog(self):
        response = self.post_save(title="Bells at Dawn")

        self.assertContains(response, 'value="Bells at Dawn"')

    def test_the_dialog_opens_empty_without_a_suggestion(self):
        response = self.post_save()

        self.assertContains(response, 'value=""')

    def test_an_already_named_mix_keeps_its_own_title(self):
        from library.models import SoundMix

        cosound = Cosound.get_or_create_from_layers([(self.sound.pk, 0.5)])
        SoundMix.objects.create(
            creator=self.user, cosound=cosound, title="My own name"
        )

        response = self.post_save(title="Bells at Dawn")

        self.assertContains(response, 'value="My own name"')
