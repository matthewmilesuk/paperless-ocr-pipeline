from django.contrib import admin

from .models import BorderlinePage, Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "uploaded_by", "source", "status", "created_at", "updated_at")
    list_filter = ("status", "source", "uploaded_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BorderlinePage)
class BorderlinePageAdmin(admin.ModelAdmin):
    list_display = ("job", "page_number", "ink_coverage_percent", "reviewed")
    list_filter = ("reviewed",)
