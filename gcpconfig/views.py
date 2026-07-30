from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render

from .forms import ConfigurationForm
from .models import Configuration, get_configuration
from .storage import (
    discard_temp_credentials_file,
    promote_credentials_file,
    write_temp_credentials_file,
)
from .validation import validate_configuration

_staff_required = user_passes_test(lambda user: user.is_staff)


@login_required
@_staff_required
def wizard(request):
    """
    Admin-only setup wizard: walks through the manual GCP console steps
    that can't be automated (see the template), then collects the three
    values pipeline/ocr.py needs. The submitted values are validated
    with one real, cheap Document AI call (validate_configuration()) --
    Configuration is only ever saved if that call actually succeeds, so
    a bad submission can't silently break OCR for everyone.
    """
    if request.method == "POST":
        form = ConfigurationForm(request.POST, request.FILES)
        if form.is_valid():
            project_id = form.cleaned_data["gcp_project_id"]
            processor_id = form.cleaned_data["gcp_docai_processor_id"]
            uploaded_file = form.cleaned_data["credentials_file"]

            tmp_path = write_temp_credentials_file(uploaded_file)
            ok, message = validate_configuration(project_id, processor_id, str(tmp_path))

            if not ok:
                discard_temp_credentials_file(tmp_path)
                form.add_error(
                    None, f"Could not validate this configuration: {message}"
                )
            else:
                final_path = promote_credentials_file(tmp_path)
                Configuration.objects.update_or_create(
                    pk=1,
                    defaults={
                        "gcp_project_id": project_id,
                        "gcp_docai_processor_id": processor_id,
                        "gcp_credentials_path": str(final_path),
                        "updated_by": request.user,
                    },
                )
                return redirect("gcpconfig_wizard")
    else:
        form = ConfigurationForm()

    return render(
        request,
        "gcpconfig/wizard.html",
        {"form": form, "configuration": get_configuration()},
    )
