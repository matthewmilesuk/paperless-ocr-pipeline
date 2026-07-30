from django.contrib import admin

from .models import Configuration


@admin.register(Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    list_display = ("gcp_project_id", "gcp_docai_processor_id", "updated_by", "updated_at")
    readonly_fields = ("updated_at",)
