"""Notify players when saved state or the sounds they use change."""

from django.db import connections
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver

from core.models import Artist, LocalPost, Manager, Player, Sound
from core.player_events import notify_players_changed


def _player_ids_for_sounds(sound_ids, using):
    sound_ids = set(sound_ids)
    if not sound_ids:
        return set()

    players = Player.objects.using(using)
    collection_match = Q(post__collection__pk__in=sound_ids)
    if connections[using].features.supports_json_field_contains:
        # A playing sound may already have been removed from the collection.
        # Query the prediction too so its display credit/file stays current.
        matches = collection_match
        for sound_id in sound_ids:
            matches |= Q(playing__layers__contains=[{"sound_id": sound_id}])
        return set(players.filter(matches).values_list("pk", flat=True).distinct())

    # The production PostgreSQL query above stays in the database. SQLite's
    # JSON implementation doesn't support contains; retain local/test support.
    player_ids = set(
        players.filter(collection_match).values_list("pk", flat=True)
    )
    for player in players.only("pk", "playing").iterator():
        if any(layer.sound_id in sound_ids for layer in player.playing.layers):
            player_ids.add(player.pk)
    return player_ids


@receiver(post_save, sender=Player, dispatch_uid="player_events.player_saved")
def player_saved(sender, instance, using, raw=False, **kwargs):
    if not raw:
        notify_players_changed([instance.pk], using=using)


@receiver(post_delete, sender=Player, dispatch_uid="player_events.player_deleted")
def player_deleted(sender, instance, using, **kwargs):
    notify_players_changed([instance.pk], using=using)


@receiver(
    m2m_changed,
    sender=LocalPost.collection.through,
    dispatch_uid="player_events.collection_changed",
)
def collection_changed(sender, instance, action, reverse, pk_set, using, **kwargs):
    if action == "pre_clear":
        matches = (
            Q(post__collection=instance.pk) if reverse else Q(post_id=instance.pk)
        )
        # Reverse clear removes the only path to the affected local posts.
        instance._player_event_collection_clear_ids = tuple(
            Player.objects.using(using).filter(matches).values_list("pk", flat=True)
        )
    elif action == "post_clear":
        player_ids = instance.__dict__.pop("_player_event_collection_clear_ids", ())
        notify_players_changed(player_ids, using=using)
    elif action in {"post_add", "post_remove"}:
        post_ids = pk_set if reverse else [instance.pk]
        player_ids = Player.objects.using(using).filter(
            post_id__in=post_ids
        ).values_list("pk", flat=True)
        notify_players_changed(player_ids, using=using)


@receiver(post_save, sender=Sound, dispatch_uid="player_events.sound_saved")
def sound_saved(sender, instance, using, raw=False, created=False, update_fields=None, **kwargs):
    if raw or created:
        return
    visible_fields = {"title", "file", "artist", "artist_id", "artist_legacy"}
    if update_fields is not None and not visible_fields.intersection(update_fields):
        return
    notify_players_changed(_player_ids_for_sounds([instance.pk], using), using=using)


@receiver(pre_delete, sender=Sound, dispatch_uid="player_events.sound_deleted")
def sound_deleted(sender, instance, using, **kwargs):
    # Capture before Django removes the collection's through-table rows.
    notify_players_changed(_player_ids_for_sounds([instance.pk], using), using=using)


@receiver(post_save, sender=Manager, dispatch_uid="player_events.manager_saved")
def manager_saved(sender, instance, using, raw=False, created=False, update_fields=None, **kwargs):
    if raw or created or (update_fields is not None and "name" not in update_fields):
        return
    notify_players_changed(
        Player.objects.using(using).filter(manager_id=instance.pk).values_list("pk", flat=True),
        using=using,
    )


@receiver(post_save, sender=Artist, dispatch_uid="player_events.artist_saved")
def artist_saved(sender, instance, using, raw=False, created=False, update_fields=None, **kwargs):
    if raw or created:
        return
    if update_fields is not None and not {"name", "url"}.intersection(update_fields):
        return
    sound_ids = Sound.objects.using(using).filter(artist_id=instance.pk).values_list("pk", flat=True)
    notify_players_changed(_player_ids_for_sounds(sound_ids, using), using=using)


@receiver(pre_delete, sender=Artist, dispatch_uid="player_events.artist_deleted")
def artist_deleted(sender, instance, using, **kwargs):
    # SET_NULL doesn't emit Sound.post_save, so capture credits before deletion.
    sound_ids = Sound.objects.using(using).filter(artist_id=instance.pk).values_list("pk", flat=True)
    notify_players_changed(_player_ids_for_sounds(sound_ids, using), using=using)
