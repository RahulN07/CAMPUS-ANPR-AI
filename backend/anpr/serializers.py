from rest_framework import serializers


class DetectionRequestSerializer(serializers.Serializer):
    """
    Validates an incoming ANPR detection request.

    NOTE FOR KING: this file was not among the files you shared for
    this change (only urls.py, utils.py, views.py, records/models.py,
    records/serializers.py, vehicles/models.py, anprService.js and
    axios.js were provided). views.py already imported
    ``DetectionRequestSerializer`` from this module, so this is a
    reconstruction based on exactly how the old view used it
    (``data["image"]``, ``data["gate"]``, ``data["source"]``,
    ``data["direction"]``).

    If your real anpr/serializers.py differs from this, keep your
    version -- the only functional requirement from the rest of this
    change is that ``direction`` stays optional. The automatic webcam
    workflow no longer sends a direction; the backend now determines
    ENTRY/EXIT itself (see ``determine_direction()`` in views.py).
    """

    image = serializers.ImageField()

    gate = serializers.IntegerField(required=False, allow_null=True)

    # Kept as "source" (not "detection_source") to match the field
    # name the existing frontend (anprService.js) and this view have
    # always used -- it still maps onto
    # EntryExitRecord.detection_source when the record is created.
    source = serializers.ChoiceField(
        choices=["WEBCAM", "UPLOAD", "CCTV", "MANUAL"],
        required=False,
        default="WEBCAM",
    )

    # Optional now: the automatic webcam workflow lets the backend
    # decide ENTRY vs EXIT. Manual/upload flows may still pass this
    # explicitly to override that.
    direction = serializers.ChoiceField(
        choices=["ENTRY", "EXIT"],
        required=False,
        allow_null=True,
    )