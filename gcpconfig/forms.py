import json

from django import forms


class ConfigurationForm(forms.Form):
    """
    The wizard's final step: the three values pipeline/ocr.py actually
    needs. Live validation against the real Document AI API happens in
    the view (gcpconfig/validation.py) after this form's own cleaning --
    this form only catches cheap, local mistakes (not valid JSON, not a
    service account key) before spending a network round-trip on
    something that was never going to work.
    """

    gcp_project_id = forms.CharField(
        label="GCP Project ID",
        max_length=255,
        help_text="e.g. paperless-ocr-pipeline -- not the project name, the ID.",
    )
    gcp_docai_processor_id = forms.CharField(
        label="Document AI Processor ID",
        max_length=255,
        help_text="The ID from the processor's details page, not its display name.",
    )
    credentials_file = forms.FileField(
        label="Service account key (JSON)",
        help_text="The JSON key file downloaded when you created the service account.",
    )

    def clean_credentials_file(self):
        uploaded_file = self.cleaned_data["credentials_file"]
        raw = uploaded_file.read()
        uploaded_file.seek(0)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise forms.ValidationError(f"Not a valid JSON file: {exc}") from exc
        if data.get("type") != "service_account":
            raise forms.ValidationError(
                "This doesn't look like a GCP service account key file "
                "(missing or wrong 'type' field -- expected 'service_account')."
            )
        return uploaded_file
