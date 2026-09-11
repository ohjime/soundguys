def serialize_mix(sm):
    layers = []
    for sl in sm.cosound.soundlayer_set.all():
        gain = float(sl.gain)
        layers.append(
            {
                **sl.sound.asLayer(with_gain=gain),
                "artwork_url": sl.sound.art.url if sl.sound.art else "",
                "mute": False,
                "isolated": False,
                "saved": True,
                "flavor": sl.sound.flavor or "",
                "tags": " / ".join(sl.sound.tags.names()) or "Unknown",
                "gain": int(round(gain * 100)),
            }
        )
    return {
        "id": sm.id,
        "cosound_id": sm.cosound_id,
        "title": sm.title,
        "created_at": sm.created_at.isoformat(),
        "layers": layers,
    }
