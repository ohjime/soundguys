from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Comment, Listener, LocalPost, Manager, Player, Post, Prediction, Sound, User
from vote.models import Vote
from vote.utils import serialize_player_for_carousel


class LocalVotePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="local-listener", email="local@example.com")
        cls.manager = Manager.objects.create(user=cls.user, name="Room manager")
        cls.sound = Sound.objects.create(
            title="Rain on the roof", file="sounds/do-not-load.wav", embeddings=[0.0] * 5
        )
        cls.other_sound = Sound.objects.create(
            title="Unused collection sound", file="sounds/unused.wav", embeddings=[0.0] * 5
        )
        playing = Prediction.new()
        playing.add_layer(cls.sound.pk, gain=0.65)
        cls.player = Player.objects.create(manager=cls.manager, name="Reading room", playing=playing)
        cls.player.post.collection.add(cls.sound, cls.other_sound)
        cls.player.post.post.title = "Listening together"
        cls.player.post.post.article = "An **article** for this room. <script>alert(1)</script>"
        cls.player.post.post.save()
        cls.params = {"player": cls.player.token, "choice": "1", "section": "west wall"}

    def page(self, *, htmx=False):
        return self.client.get(
            reverse("vote:vote_tab" if htmx else "vote:vote"),
            self.params,
            HTTP_HX_REQUEST="true" if htmx else "false",
        )

    def comment_url(self, name="create_comment", post=None, params=None):
        return reverse(f"vote:{name}", kwargs={"slug": (post or self.player.post).post.slug}) + "?" + urlencode(params or self.params)

    def test_nfc_page_has_two_tabs_and_post_without_audio(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What is this?")
        self.assertNotContains(response, 'id="user-details"')
        self.assertContains(response, "Listening together")
        self.assertContains(response, "An <strong>article</strong> for this room.")
        self.assertContains(response, "Discussion")
        self.assertContains(response, "Rain on the roof")
        self.assertNotContains(response, "Unused collection sound")
        self.assertNotContains(response, "sounds/do-not-load.wav")
        self.assertNotContains(response, '"sound_file"')
        self.assertNotContains(response, "data-cosound-mixer-root")
        self.assertNotContains(response, "mountStore(")
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, "VOTE SENT")
        self.assertFalse(Vote.objects.exists())
        self.assertEqual(response.context["player"].pk, self.player.pk)
        self.assertEqual(response.context["section"], "west wall")

    def test_metadata_does_not_resolve_sound_files_or_change_gains(self):
        with patch.object(Sound, "asLayer", side_effect=AssertionError("Audio serializer called")):
            layers = serialize_player_for_carousel(self.player)
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["sound_gain"], 0.65)
        self.assertNotIn("sound_file", layers[0])

    def test_tab_reads_latest_prediction_and_listener_likes(self):
        listener = Listener.objects.create(user=self.user)
        listener.collection.add(self.other_sound)
        self.client.force_login(self.user)
        playing = Prediction.new()
        playing.add_layer(self.other_sound.pk, gain=0.2)
        self.player.update(playing)
        response = self.page(htmx=True)
        self.assertContains(response, "Unused collection sound")
        self.assertNotContains(response, "Rain on the roof")
        self.assertEqual(response.context["layers"][0]["sound_gain"], 0.2)
        self.assertTrue(response.context["layers"][0]["saved"])

    def test_empty_prediction_and_invalid_player_have_clear_empty_states(self):
        self.player.update(Prediction.new())
        response = self.page(htmx=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["layers"], [])
        response = self.client.get(reverse("vote:vote"), {"player": "missing"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["player"])

    def test_about_reuses_the_home_article(self):
        response = self.client.get(reverse("vote:about"), self.params, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Sound, chosen by the room")
        self.assertNotContains(response, "vote-display-layers")

    def test_draft_writing_and_comments_are_hidden_but_prediction_still_renders(self):
        self.player.post.post.publication_date = None
        self.player.post.post.save(update_fields=["publication_date"])
        response = self.page(htmx=True)
        self.assertContains(response, "Rain on the roof")
        self.assertNotContains(response, "Listening together")
        self.assertNotContains(response, "An <strong>article</strong>")
        self.assertEqual(self.client.get(self.comment_url("discussion")).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.comment_url(), {"body": "Hidden"}).status_code, 404)

    def test_comments_belong_to_base_post_and_are_permanent_per_listener(self):
        self.client.force_login(self.user)
        response = self.client.post(self.comment_url(), {"body": "My first response"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "My first response")
        comment = Comment.objects.get()
        self.assertEqual(type(comment.post), Post)
        self.assertEqual(comment.post_id, self.player.post.post_id)
        response = self.client.post(self.comment_url(), {"body": "A replacement"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "already commented")
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(Comment.objects.get().body, "My first response")

    def test_comment_auth_validation_and_http_methods(self):
        self.assertEqual(self.client.post(self.comment_url(), {"body": "Hello"}).status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.comment_url()).status_code, 405)
        for body in ("   ", "x" * 2001):
            response = self.client.post(self.comment_url(), {"body": body}, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(Comment.objects.exists())
        response = self.client.post(self.comment_url(), {"body": "Without HTMX"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["player"], [self.player.token])
        self.assertEqual(urlsplit(response.url).fragment, f"local-discussion-{self.player.post_id}")

    def test_post_switch_rejects_old_forms_and_keeps_old_discussion(self):
        old_post = self.player.post
        Comment.objects.create(post=old_post.post, user=self.user, body="Old discussion")
        replacement = LocalPost.objects.create(post=Post.objects.create(title="New program", composer=self.user, publication_date=timezone.now()))
        self.player.post = replacement
        self.player.save(update_fields=["post"])
        self.client.force_login(self.user)
        response = self.client.post(self.comment_url(post=old_post), {"body": "Stale form"})
        self.assertEqual(response.status_code, 404)
        response = self.client.post(self.comment_url(post=replacement), {"body": "New discussion"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "New discussion")
        self.assertNotContains(response, "Old discussion")
        self.assertTrue(old_post.post.comments.filter(body="Old discussion").exists())

    def test_comments_escape_text_and_paginate_with_nfc_context(self):
        for index in range(11):
            user = User.objects.create_user(username=f"room-{index}", email=f"room-{index}@example.com")
            Comment.objects.create(post=self.player.post.post, user=user, body=f"<script>comment {index}</script>")
        response = self.client.get(self.comment_url("discussion"))
        self.assertNotContains(response, "<script>comment")
        self.assertContains(response, "&lt;script&gt;")
        page_url = next(item["url"] for item in response.context["pagination_items"] if item.get("number") == 2)
        self.assertEqual(parse_qs(urlsplit(page_url).query), {**{key: [value] for key, value in self.params.items()}, "page": ["2"]})
        self.assertContains(self.client.get(page_url), "comment 0")

    def test_login_partial_updates_the_header_identity(self):
        request = RequestFactory().get(reverse("vote:vote"))
        request.user = self.user
        html = render_to_string("vote/index.html#post_login", request=request)
        self.assertIn('id="core-header"', html)
        self.assertIn('hx-swap-oob="outerHTML"', html)
        self.assertIn(self.user.username, html)

    def test_unauthenticated_save_and_vote_open_shared_guest_login(self):
        for url in (reverse("library:save"), reverse("vote:submit_vote") + "?" + urlencode(self.params)):
            response = self.client.post(
                url,
                HTTP_HX_REQUEST="true",
                HTTP_HX_CURRENT_URL="http://testserver/vote/?" + urlencode(self.params),
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Continue Anonymously")
            self.assertEqual(response.headers["HX-Retarget"], "#core_modal_content")
            self.assertFalse(Vote.objects.exists())
