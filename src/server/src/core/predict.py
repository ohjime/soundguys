import random
from collections import Counter, defaultdict
from datetime import timedelta

from django.db import transaction
from django.tasks import task
from django.utils import timezone

from core.models import Player, Prediction, Sound
from vote.models import Vote


ACTIVITY_WINDOW = timedelta(minutes=5)


def activate_player(player: Player) -> Prediction | None:
    """Wake ``player`` with one random sound from its current collection."""
    # Hold a row lock on the candidates until the activation is persisted. In
    # submit_vote this nests inside the Player lock, so deleting the selected
    # sound cannot leave the response pointing at an already-missing layer.
    with transaction.atomic():
        sound_ids = list(
            player.post.collection.select_for_update()
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not sound_ids:
            player.update(Prediction.new())
            return None

        prediction = Prediction.new()
        prediction.add_layer(sound_id=random.choice(sound_ids), gain=1.0)
        player.playing = prediction
        player.activated_at = timezone.now()
        player.save(update_fields=["playing", "activated_at"])
        return prediction


def _predict_for_player(player_id: int) -> int:
    prediction_to_announce = None
    with transaction.atomic():
        player = (
            Player.objects.select_for_update()
            .select_related("post")
            .get(pk=player_id)
        )
        if player.sleeping:
            return 0

        recent_votes = Vote.recent(
            player,
            minutes=int(ACTIVITY_WINDOW.total_seconds() // 60),
        )
        if not recent_votes:
            if (
                player.activated_at is not None
                and player.activated_at >= timezone.now() - ACTIVITY_WINDOW
                and player.playing
            ):
                return 0
            player.update(Prediction.new())
            return 0

        active_listeners = sorted(
            Vote.get_listeners(recent_votes),
            key=lambda listener: listener.pk,
        )
        next_prediction = Prediction.new()
        selected_sound_ids: set[int] = set()

        library_by_tag: dict[int, list[Sound]] = defaultdict(list)
        for sound in player.post.collection.prefetch_related("tags"):
            for tag in sound.tags.all():
                library_by_tag[tag.pk].append(sound)

        for listener in active_listeners:
            tag_counts: Counter[int] = Counter()
            for sound in listener.collection.prefetch_related("tags"):
                tag_counts.update(tag.pk for tag in sound.tags.all())

            if not tag_counts:
                continue

            highest_count = max(tag_counts.values())
            usable_top_tags = [
                tag_id
                for tag_id, count in tag_counts.items()
                if count == highest_count and library_by_tag[tag_id]
            ]
            if not usable_top_tags:
                continue

            selected_tag = random.choice(usable_top_tags)
            unused_sounds = [
                sound
                for sound in library_by_tag[selected_tag]
                if sound.pk not in selected_sound_ids
            ]
            if not unused_sounds:
                continue

            selected_sound = random.choice(unused_sounds)
            next_prediction.add_layer(sound_id=selected_sound.pk, gain=1.0)
            selected_sound_ids.add(selected_sound.pk)

        if next_prediction:
            player.update(next_prediction)
            prediction_to_announce = next_prediction

    if prediction_to_announce is not None:
        player.announce(prediction_to_announce)
        return 1
    return 0


@task
def random_predictor(
    player_id: int,
    *args,
    **kwargs,
) -> int:
    return _predict_for_player(player_id)
