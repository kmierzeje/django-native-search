from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma
from .models import Lexem, Index

@admin.register(Lexem)
class LexemAdmin(admin.ModelAdmin):
    list_display=['surface']
    search_fields=['surface']
    

@admin.register(Index)
class IndexAdmin(admin.ModelAdmin):
    readonly_fields = ['app_label', 'model', 'entries', 'occurrences']
    list_display = ['__str__', 'entries', 'occurrences']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def entries(self, obj):
        return intcomma(obj.entries())
    def occurrences(self, obj):
        return intcomma(obj.occurrences())
        