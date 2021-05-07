from django.db import models
from django.db.models.signals import post_save

class OccurrencesField(models.ManyToManyField):
    def __init__(self, query_name=None, **kwargs):
        from .models import Lexem
        kwargs.setdefault('to', Lexem._meta.label)
        kwargs.setdefault('related_name','+')
        kwargs.setdefault('editable', False)
        super().__init__(**kwargs)
        self.query_name=query_name
        
    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["query_name"]=self.query_name
        return name, path, args, kwargs
        
    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        occurrences = self.remote_field.through
        if not occurrences or isinstance(occurrences, str):
            return
        
        models.PositiveIntegerField(db_index=True).contribute_to_class(
            occurrences, 'position')
        
        models.CharField(db_index=True, max_length=16, default=' ').contribute_to_class(
            occurrences, 'prefix')
        
        unique = occurrences._meta.unique_together[0]+('position',)
        occurrences._meta.unique_together=(unique,)
        occurrences._meta.ordering=['position']
        occurrences._meta.verbose_name=f"{cls._meta.model_name}-occurrence"
        occurrences._meta.verbose_name_plural=f"{cls._meta.model_name}-occurrences"
        remote_field=occurrences._meta.get_field(cls._meta.model_name).remote_field
        remote_field.related_query_name=self.query_name
        
        class RelatedAccessor(remote_field.field.related_accessor_class):
            
            def __get__(self, instance, cls=None):
                if instance:
                    return super().__get__(instance, cls)
                
                return self.rel.related_model._meta.default_manager
        
        setattr(cls, self.name, RelatedAccessor(remote_field))
        
        post_save.connect(self.update_occurrences)
        
    def update_occurrences(self, instance, **kwargs):
        if not isinstance(instance, self.model):
            return
        lexem_model=self.remote_field.model
        max_length=lexem_model.surface.field.max_length
        instance.occurrences.all().delete()
        
        lexem_cache={}
        for position, surface in enumerate(instance.tokens):
            if len(surface)>max_length:
                continue
            
            lexem=lexem_cache.get(surface) or lexem_cache.setdefault(
                surface, lexem_model.objects.get_or_create(surface=surface)[0])
            values=dict(lexem=lexem, position=position)
            prefix=getattr(surface,'prefix',None)
            if prefix is not None:
                values['prefix']=prefix
            instance.occurrences.create(**values)
