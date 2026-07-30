from django import forms


class UploadForm(forms.Form):
    """Upload form for the web UI entry point into the pipeline."""

    file = forms.FileField(
        label="PDF to process",
        help_text="Duplex scan, single PDF in -> single PDF/A out.",
    )
