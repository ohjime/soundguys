"""Player collections survive the LocalPost schema transition and rollback."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LocalPostMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0004_post_title_length")]
    migrate_to = [("core", "0005_localpost_player_post")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        super().setUp()
        apps = self.migrate(self.migrate_from)
        User = apps.get_model("core", "User")
        Manager = apps.get_model("core", "Manager")
        Player = apps.get_model("core", "Player")
        Post = apps.get_model("core", "Post")
        Sound = apps.get_model("core", "Sound")
        self.user = User.objects.create(username="migration-manager", email="player-migration@example.com")
        self.manager = Manager.objects.create(user=self.user, name="Existing manager")
        self.shared_post = Post.objects.create(title="Existing shared writing", composer=self.user)
        self.shared_data = Post.objects.values().get(pk=self.shared_post.pk)
        self.sounds = [
            Sound.objects.create(file=f"sounds/{index}.mp3", title=f"Sound {index}", embeddings=[0] * 5)
            for index in range(3)
        ]
        self.players = []
        for index, sound_indexes in enumerate(((0, 1), (1, 2), ())):
            player = Player.objects.create(
                manager=self.manager,
                name="A" * 255 if index == 0 else f"Player {index}",
                bio=f"Existing player {index} description",
                location=f"Venue {index}",
                photo=f"photos/player-{index}.png",
                token=f"existing-player-token-{index}",
                playing={"layers": [{"sound_id": self.sounds[0].pk, "sound_gain": 0.35}]},
            )
            player.sounds.add(*(self.sounds[sound_index] for sound_index in sound_indexes))
            self.players.append(player)
        self.old_players = list(Player.objects.order_by("pk").values())
        self.old_collections = {
            player.pk: set(player.sounds.values_list("pk", flat=True)) for player in self.players
        }

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_player_permissions_follow_the_collection_without_broadening_other_accounts(self):
        apps = MigrationExecutor(connection).loader.project_state(self.migrate_from).apps
        ContentType = apps.get_model("contenttypes", "ContentType")
        Permission = apps.get_model("auth", "Permission")
        Group = apps.get_model("auth", "Group")
        User = apps.get_model("core", "User")
        player_type, _ = ContentType.objects.get_or_create(app_label="core", model="player")
        direct_users = {}
        groups = {}
        original_permission_ids = {}
        group_member = User.objects.create(username="group-editor", email="group-editor@example.com")
        for action in ("add", "change", "view", "delete"):
            permission, _ = Permission.objects.get_or_create(
                content_type=player_type,
                codename=f"{action}_player",
                defaults={"name": f"Can {action} player"},
            )
            user = User.objects.create(username=f"{action}-editor", email=f"{action}-editor@example.com")
            user.user_permissions.add(permission)
            group = Group.objects.create(name=f"Player {action} editors")
            group.permissions.add(permission)
            group_member.groups.add(group)
            direct_users[action] = user
            groups[action] = group
            original_permission_ids[action] = permission.pk
        unprivileged_group = Group.objects.create(name="Listeners")
        unprivileged = User.objects.create(username="no-player-access", email="no-player-access@example.com")
        unprivileged.groups.add(unprivileged_group)
        # An unrelated shared-post grant should survive both directions as-is.
        post_type, _ = ContentType.objects.get_or_create(app_label="core", model="post")
        shared_permission, _ = Permission.objects.get_or_create(
            content_type=post_type, codename="view_post", defaults={"name": "Can view post"}
        )
        unprivileged.user_permissions.add(shared_permission)

        self.migrate(self.migrate_to)
        for action in direct_users:
            expected = {f"{action}_localpost"}
            if action == "change":
                expected.add("view_localpost")
            self.assertEqual(
                set(direct_users[action].user_permissions.filter(content_type__model="localpost").values_list("codename", flat=True)),
                expected,
            )
            self.assertEqual(
                set(groups[action].permissions.filter(content_type__model="localpost").values_list("codename", flat=True)),
                expected,
            )
            self.assertTrue(direct_users[action].user_permissions.filter(pk=original_permission_ids[action]).exists())
            self.assertTrue(groups[action].permissions.filter(pk=original_permission_ids[action]).exists())
        self.assertEqual(group_member.groups.count(), 4)
        self.assertFalse(group_member.user_permissions.exists())
        self.assertFalse(unprivileged.user_permissions.filter(content_type__model="localpost").exists())
        self.assertFalse(unprivileged_group.permissions.exists())
        self.assertEqual(list(unprivileged.user_permissions.values_list("pk", flat=True)), [shared_permission.pk])

        self.migrate(self.migrate_from)
        self.assertFalse(ContentType.objects.filter(app_label="core", model="localpost").exists())
        for action in direct_users:
            self.assertEqual(list(direct_users[action].user_permissions.values_list("pk", flat=True)), [original_permission_ids[action]])
            self.assertEqual(list(groups[action].permissions.values_list("pk", flat=True)), [original_permission_ids[action]])
        self.assertEqual(list(unprivileged.user_permissions.values_list("pk", flat=True)), [shared_permission.pk])
        self.assertEqual(group_member.groups.count(), 4)

    def test_player_data_and_sound_membership_move_to_individual_local_posts(self):
        apps = self.migrate(self.migrate_to)
        Player = apps.get_model("core", "Player")
        Post = apps.get_model("core", "Post")
        LocalPost = apps.get_model("core", "LocalPost")
        Comment = apps.get_model("core", "Comment")
        self.assertEqual(Post.objects.values().get(pk=self.shared_post.pk), self.shared_data)
        self.assertEqual(LocalPost.objects.count(), len(self.players))
        self.assertEqual(
            list(Player.objects.order_by("pk").values(*self.old_players[0].keys())),
            self.old_players,
        )
        post_ids = set()
        for player in Player.objects.select_related("post").all():
            post_ids.add(player.post_id)
            self.assertGreater(player.post_id, self.shared_post.pk)
            self.assertEqual(player.post.title, player.name)
            self.assertEqual(player.post.article, player.bio)
            self.assertEqual(player.post.composer_id, self.user.pk)
            self.assertIsNotNone(player.post.publication_date)
            self.assertEqual(
                set(player.post.collection.values_list("pk", flat=True)),
                self.old_collections[player.pk],
            )
        self.assertEqual(len(post_ids), len(self.players))
        new_post = Post.objects.create(title="Post after migration", composer_id=self.user.pk)
        self.assertGreater(new_post.pk, max(post_ids))
        self.assertNotIn("core_player_sounds", connection.introspection.table_names())

        player = Player.objects.get(pk=self.players[0].pk)
        player.post.collection.set([self.sounds[2].pk])
        Post.objects.filter(pk=player.post_id).update(article="Edited local writing")
        comment = Comment.objects.create(post_id=player.post_id, user_id=self.user.pk, body="Keep this reply")
        post_id = player.post_id

        apps = self.migrate(self.migrate_from)
        Player = apps.get_model("core", "Player")
        Post = apps.get_model("core", "Post")
        Comment = apps.get_model("core", "Comment")
        self.assertEqual(list(Player.objects.order_by("pk").values()), self.old_players)
        self.assertEqual(set(Player.objects.get(pk=player.pk).sounds.values_list("pk", flat=True)), {self.sounds[2].pk})
        for old_player in self.players[1:]:
            self.assertEqual(
                set(Player.objects.get(pk=old_player.pk).sounds.values_list("pk", flat=True)),
                self.old_collections[old_player.pk],
            )
        self.assertEqual(Post.objects.get(pk=post_id).article, "Edited local writing")
        self.assertEqual(Comment.objects.get(pk=comment.pk).body, "Keep this reply")
        self.assertEqual(Post.objects.values().get(pk=self.shared_post.pk), self.shared_data)

        # Reapplying after a rollback must use the collection as it exists now.
        apps = self.migrate(self.migrate_to)
        Player = apps.get_model("core", "Player")
        Post = apps.get_model("core", "Post")
        self.assertEqual(
            set(Player.objects.get(pk=player.pk).post.collection.values_list("pk", flat=True)),
            {self.sounds[2].pk},
        )
        self.assertTrue(Post.objects.filter(pk=post_id, article="Edited local writing").exists())


class PlayerSleepingMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0006_selectable_local_post")]
    migrate_to = [("core", "0007_player_sleeping")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        super().setUp()
        apps = self.migrate(self.migrate_from)
        User = apps.get_model("core", "User")
        Manager = apps.get_model("core", "Manager")
        Player = apps.get_model("core", "Player")
        Post = apps.get_model("core", "Post")
        LocalPost = apps.get_model("core", "LocalPost")
        Sound = apps.get_model("core", "Sound")

        user = User.objects.create(
            username="sleep-migration",
            email="sleep-migration@example.com",
        )
        manager = Manager.objects.create(user=user, name="Manager")
        sound = Sound.objects.create(
            file="sounds/migration.mp3",
            title="Migration sound",
            embeddings=[0] * 5,
        )
        empty_post = LocalPost.objects.create(
            post=Post.objects.create(title="Empty", composer=user)
        )
        active_post = LocalPost.objects.create(
            post=Post.objects.create(title="Active", composer=user)
        )
        self.empty_id = Player.objects.create(
            manager=manager,
            name="Empty player",
            post=empty_post,
            token="empty-player-token",
            playing={"layers": []},
        ).pk
        self.active_id = Player.objects.create(
            manager=manager,
            name="Active player",
            post=active_post,
            token="active-player-token",
            playing={
                "layers": [{"sound_id": sound.pk, "sound_gain": 1.0}]
            },
        ).pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_empty_players_sleep_and_playing_players_stay_awake(self):
        apps = self.migrate(self.migrate_to)
        Player = apps.get_model("core", "Player")

        empty = Player.objects.get(pk=self.empty_id)
        active = Player.objects.get(pk=self.active_id)
        self.assertTrue(empty.sleeping)
        self.assertFalse(active.sleeping)
        self.assertIsNone(empty.activated_at)
        self.assertIsNone(active.activated_at)
