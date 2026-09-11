"""Exercise the real schema transition with existing posts and permissions."""

import datetime
import uuid

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SharedPostMigrationTests(TransactionTestCase):
    migrate_from = [
        ("explore", "0007_remove_post_farewell_style"),
        ("core", "0002_player_location_sound_artist_legacy_alter_player_bio_and_more"),
    ]
    migrate_to = [("explore", "0008_publicpost_shared_content")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        super().setUp()
        self.old_apps = self.migrate(self.migrate_from)
        User = self.old_apps.get_model("core", "User")
        Cosound = self.old_apps.get_model("core", "Cosound")
        Post = self.old_apps.get_model("explore", "Post")
        Comment = self.old_apps.get_model("explore", "Comment")
        self.user = User.objects.create(username="migration-listener", email="migration@example.com")
        self.cosound = Cosound.objects.create(hashset="migration-mix", hashid="migration-hash")
        self.published = Post.objects.create(
            pk=41,
            composer=self.user,
            cosound=self.cosound,
            title="An existing article",
            announcers_call="Listen closely",
            greeting_style="Welcome",
            font_family="serif",
            article="# A preserved article\n\nWith **markdown** and café.",
            authors=[{"name": "Alex", "role": "Writer", "url": "https://example.com/"}],
            slug=uuid.UUID("a158ce55-fc66-4869-b93b-4fb77872d31a"),
            publication_date=datetime.datetime(2023, 2, 3, 4, 5, tzinfo=datetime.UTC),
        )
        self.draft = Post.objects.create(
            pk=42,
            composer=self.user,
            title="An existing draft",
            slug=uuid.UUID("b158ce55-fc66-4869-b93b-4fb77872d31a"),
        )
        self.comment = Comment.objects.create(
            pk=61, post=self.published, user=self.user, body="An existing response."
        )
        Comment.objects.create(pk=62, post=self.draft, user=self.user, body="Draft response.")
        Post.objects.update(
            created_at=datetime.datetime(2022, 6, 7, 8, 9, tzinfo=datetime.UTC),
            updated_at=datetime.datetime(2023, 6, 7, 8, 9, tzinfo=datetime.UTC),
        )
        Comment.objects.update(
            created_at=datetime.datetime(2024, 6, 7, 8, 9, tzinfo=datetime.UTC)
        )
        self.old_posts = list(Post.objects.order_by("pk").values())
        self.old_comments = list(Comment.objects.order_by("pk").values())

    def tearDown(self):
        # Restore the current schema before TransactionTestCase flushes tables
        # or subsequent tests use the runtime models.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def assert_forward_data(self, apps):
        Post = apps.get_model("core", "Post")
        PublicPost = apps.get_model("explore", "PublicPost")
        Comment = apps.get_model("core", "Comment")
        expected_posts = [{key: value for key, value in row.items() if key != "cosound_id"}
                          for row in self.old_posts]
        self.assertEqual(list(Post.objects.order_by("pk").values()), expected_posts)
        self.assertEqual(list(Comment.objects.order_by("pk").values()), self.old_comments)
        self.assertEqual(
            list(PublicPost.objects.order_by("pk").values_list("post_id", "cosound_id")),
            [(self.published.pk, self.cosound.pk), (self.draft.pk, None)],
        )
        self.assertEqual(Comment.objects.get(pk=self.comment.pk).post.pk, self.published.pk)
        tables = connection.introspection.table_names()
        self.assertNotIn("explore_post", tables)
        self.assertNotIn("explore_comment", tables)

    def test_existing_content_and_ids_survive_forward_and_full_rollback(self):
        apps = self.migrate(self.migrate_to)
        self.assert_forward_data(apps)
        Post = apps.get_model("core", "Post")
        Comment = apps.get_model("core", "Comment")
        # Explicitly copied IDs must also advance the PostgreSQL sequences.
        new_post = Post.objects.create(composer_id=self.user.pk, title="A new shared post")
        self.assertGreater(new_post.pk, self.draft.pk)
        new_comment = Comment.objects.create(
            post=new_post, user_id=self.user.pk, body="A new shared response"
        )
        self.assertGreater(new_comment.pk, 62)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Comment.objects.create(post=new_post, user_id=self.user.pk, body="Duplicate")

        apps = self.migrate(self.migrate_from)
        Post = apps.get_model("explore", "Post")
        Comment = apps.get_model("explore", "Comment")
        self.assertEqual(list(Post.objects.filter(pk__lte=42).order_by("pk").values()), self.old_posts)
        self.assertEqual(list(Comment.objects.filter(pk__lte=62).order_by("pk").values()), self.old_comments)
        self.assertEqual(Post.objects.get(pk=new_post.pk).article, new_post.article)
        self.assertIsNone(Post.objects.get(pk=new_post.pk).cosound_id)
        self.assertEqual(Comment.objects.get(pk=new_comment.pk).body, new_comment.body)
        restored_post = Post.objects.create(composer_id=self.user.pk, title="After rollback")
        self.assertGreater(restored_post.pk, new_post.pk)
        restored_comment = Comment.objects.create(
            post=restored_post, user_id=self.user.pk, body="After rollback"
        )
        self.assertGreater(restored_comment.pk, new_comment.pk)

    def test_explore_only_rollback_can_be_edited_and_reapplied(self):
        self.assert_forward_data(self.migrate(self.migrate_to))
        apps = self.migrate([("explore", "0007_remove_post_farewell_style")])
        # project_state(targets) omits the independently still-applied core.0003;
        # the database must retain its empty tables until core is rolled back.
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM core_post")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM core_comment")
            self.assertEqual(cursor.fetchone()[0], 0)
        Post = apps.get_model("explore", "Post")
        Comment = apps.get_model("explore", "Comment")
        Post.objects.filter(pk=self.published.pk).update(article="Edited after rollback")
        Comment.objects.filter(pk=self.comment.pk).delete()
        apps = self.migrate(self.migrate_to)
        self.assertEqual(
            apps.get_model("core", "Post").objects.get(pk=self.published.pk).article,
            "Edited after rollback",
        )
        self.assertFalse(apps.get_model("core", "Comment").objects.filter(pk=self.comment.pk).exists())

    def test_admin_permissions_grants_and_history_keep_their_identity(self):
        # Include the already-applied admin app in this state so historical log
        # entries are created without importing the current runtime models.
        apps = MigrationExecutor(connection).loader.project_state().apps
        ContentType = apps.get_model("contenttypes", "ContentType")
        Permission = apps.get_model("auth", "Permission")
        Group = apps.get_model("auth", "Group")
        User = apps.get_model("core", "User")
        LogEntry = apps.get_model("admin", "LogEntry")
        user = User.objects.get(pk=self.user.pk)
        group = Group.objects.create(name="Article editors")
        user.groups.add(group)
        post_type, _ = ContentType.objects.get_or_create(app_label="explore", model="post")
        comment_type, _ = ContentType.objects.get_or_create(app_label="explore", model="comment")
        change_post, _ = Permission.objects.get_or_create(
            content_type=post_type, codename="change_post", defaults={"name": "Can change post"}
        )
        delete_post, _ = Permission.objects.get_or_create(
            content_type=post_type, codename="delete_post", defaults={"name": "Can delete post"}
        )
        delete_comment, _ = Permission.objects.get_or_create(
            content_type=comment_type, codename="delete_comment", defaults={"name": "Can delete comment"}
        )
        user.user_permissions.add(change_post, delete_comment)
        group.permissions.add(delete_post)
        log = LogEntry.objects.create(
            user_id=user.pk, content_type=post_type, object_id=str(self.published.pk),
            object_repr=self.published.title, action_flag=2, change_message="Existing edit",
        )
        # Simulate running core migrations separately: post_migrate may already
        # have created target content types and permissions with their own grants.
        target_type, _ = ContentType.objects.get_or_create(app_label="core", model="comment")
        target_permission, _ = Permission.objects.get_or_create(
            content_type=target_type, codename="delete_comment", defaults={"name": "Can delete comment"}
        )
        group.permissions.add(target_permission)

        self.migrate(self.migrate_to)
        self.assertEqual(ContentType.objects.get(pk=post_type.pk).model, "publicpost")
        self.assertEqual(ContentType.objects.get(pk=comment_type.pk).app_label, "core")
        self.assertEqual(Permission.objects.get(pk=change_post.pk).codename, "change_publicpost")
        self.assertTrue(user.user_permissions.filter(pk=change_post.pk).exists())
        self.assertTrue(group.permissions.filter(pk=delete_post.pk).exists())
        self.assertTrue(group.permissions.filter(pk=delete_comment.pk).exists())
        self.assertTrue(user.user_permissions.filter(content_type__app_label="core", codename="change_post").exists())
        self.assertTrue(group.permissions.filter(content_type__app_label="core", codename="delete_post").exists())
        self.assertEqual(LogEntry.objects.get(pk=log.pk).content_type_id, post_type.pk)

        self.migrate(self.migrate_from)
        self.assertEqual(ContentType.objects.get(pk=post_type.pk).model, "post")
        self.assertEqual(ContentType.objects.get(pk=post_type.pk).app_label, "explore")
        self.assertEqual(ContentType.objects.get(pk=comment_type.pk).app_label, "explore")
        self.assertEqual(Permission.objects.get(pk=change_post.pk).codename, "change_post")
        self.assertTrue(user.user_permissions.filter(pk=change_post.pk).exists())
        self.assertTrue(group.permissions.filter(pk=delete_post.pk).exists())
        self.assertTrue(group.permissions.filter(pk=delete_comment.pk).exists())
        self.assertEqual(LogEntry.objects.get(pk=log.pk).content_type_id, post_type.pk)
