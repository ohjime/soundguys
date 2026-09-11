import json

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.forms import CommentForm
from core.models import Comment, Cosound, Listener, Player, Sound
from core.predict import activate_player
from core.utils import show_modal
from login.views import _authenticate_anonymously
from vote.models import Vote
from vote.utils import (
    build_vote_context,
    build_vote_page_context,
    get_throttle_seconds_left,
    local_discussion_context,
    serialize_player_for_carousel,
    serialize_recent_votes,
)


@require_GET
def voter_index(request):
    return render(request, "vote/index.html", build_vote_page_context(request))


@require_GET
def vote_initial(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")
    return render(request, "vote/index.html#initial", build_vote_page_context(request))


@require_GET
def vote_tab(request):
    return render(request, "vote/index.html#tab_vote", build_vote_page_context(request))


@require_GET
def vote_about(request):
    return render(request, "app/home.html#tab_about")


def get_local_post(request, slug):
    player = get_object_or_404(
        Player.objects.select_related("post__post"),
        token=request.GET.get("player", ""),
        post__post__slug=slug,
        post__post__publication_date__isnull=False,
    )
    return player.post


@require_GET
def local_discussion(request, slug):
    post = get_local_post(request, slug)
    context = local_discussion_context(request, post, request.GET.get("page", 1))
    return render(request, "vote/discussion.html", context)


@require_POST
def create_local_comment(request, slug):
    post = get_local_post(request, slug)
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to comment.", status=403)
    form = CommentForm(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                Comment.objects.create(
                    post=post.post, user=request.user, body=form.cleaned_data["body"]
                )
        except IntegrityError:
            # One response per listener, including simultaneous submissions.
            pass
        if not request.htmx:
            query = request.GET.copy()
            query.pop("page", None)
            return redirect(
                f"{reverse('vote:vote')}?{query.urlencode()}#local-discussion-{post.pk}"
            )
        form = None
    return render(
        request, "vote/discussion.html", local_discussion_context(request, post, form=form)
    )


@require_POST
def submit_vote(request):
    if not request.htmx:
        return HttpResponse("Request Denied.")

    context = build_vote_context(request)
    player = context["player"]
    choice = context["choice"]
    activation_requested = request.POST.get("activation") == "1"
    anonymous_requested = request.POST.get("anonymous") == "1"

    if player is None or choice not in ("0", "1"):
        response = HttpResponse("")
        response["HX-Trigger"] = json.dumps(
            {"vote-throttled": {"seconds_left": 60}}
        )
        return response

    if not request.user.is_authenticated:
        if anonymous_requested:
            try:
                _authenticate_anonymously(request)
            except Exception:
                response = HttpResponse("")
                event = (
                    "player-activation-unavailable"
                    if activation_requested
                    else "vote-auth-unavailable"
                )
                message = (
                    "Could not activate anonymously. Please try again."
                    if activation_requested
                    else "Could not vote anonymously. Please try again."
                )
                response["HX-Trigger"] = json.dumps({event: {"message": message}})
                return response
        else:
            request.session["post_login_partial"] = "vote/index.html#post_login"
            response = show_modal(
                request,
                "login/index.html#modal",
                {"allow_anonymous": True},
            )
            response["HX-Trigger"] = "auth-required"
            return response

    prediction_to_announce = None

    with transaction.atomic():
        player = (
            Player.objects.select_for_update()
            .select_related("post")
            .get(pk=player.pk)
        )

        # Predictions store sound IDs rather than database relations. Lock all
        # currently referenced sounds in a deterministic order so a concurrent
        # deletion cannot invalidate them between validation and vote creation.
        prediction_layers = list(player.playing.layers)
        prediction_sound_ids = sorted(
            {layer.sound_id for layer in prediction_layers}
        )
        locked_sound_ids = set(
            Sound.objects.select_for_update()
            .filter(pk__in=prediction_sound_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        # Treat a prediction whose sounds have since been deleted as needing
        # repair, matching what the voting card and physical player can use.
        rendered_layers = serialize_player_for_carousel(
            player,
            request.user,
            choice,
        )
        has_unresolved_layers = (
            len(locked_sound_ids) != len(prediction_sound_ids)
            or len(rendered_layers) != len(prediction_layers)
        )
        needs_activation = (
            player.sleeping or not rendered_layers or has_unresolved_layers
        )

        if needs_activation or activation_requested:
            if needs_activation:
                prediction_to_announce = activate_player(player)
                if prediction_to_announce is None:
                    response = HttpResponse("")
                    response["HX-Trigger"] = json.dumps(
                        {
                            "player-activation-unavailable": {
                                "message": "This room has no sounds available yet."
                            }
                        }
                    )
                    return response
                rendered_layers = serialize_player_for_carousel(
                    player,
                    request.user,
                    choice,
                )

            response = HttpResponse("")
            response["HX-Trigger"] = json.dumps(
                {
                    "player-activated": {
                        "layers": rendered_layers
                    }
                }
            )
        else:
            listener, _ = Listener.objects.get_or_create(user=request.user)
            seconds_left = get_throttle_seconds_left(listener)
            if seconds_left > 0:
                response = HttpResponse("")
                response["HX-Trigger"] = json.dumps(
                    {"vote-throttled": {"seconds_left": seconds_left}}
                )
                return response

            layers = [
                (layer.sound_id, layer.sound_gain) for layer in player.playing.layers
            ]
            cosound = Cosound.get_or_create_from_layers(layers)
            value = int(choice)
            Vote.objects.create(
                voter=listener,
                player=player,
                cosound=cosound,
                value=value,
                section=context.get("section") or "",
            )

            response = HttpResponse("")
            response["HX-Trigger"] = json.dumps(
                {"vote-success": {"voters": serialize_recent_votes(player)}}
            )

    if prediction_to_announce is not None:
        player.announce(prediction_to_announce)
    return response
