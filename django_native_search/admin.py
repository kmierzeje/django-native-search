from django.contrib import admin

from .models import Lexem

@admin.register(Lexem)
class LexemAdmin(admin.ModelAdmin):
    list_display=['surface']
    search_fields=['surface']