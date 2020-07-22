from django.db import models
from django.db.models.functions import Lower
import django_expression_index

from django.template.loader import render_to_string
from django.utils.functional import cached_property
from django.db.models.signals import post_save
from django.dispatch import receiver

from .manager import IndexManager

class Lexem(models.Model):
    surface=models.CharField(max_length=255, db_index=True, unique=True)
    
    class Meta:
        indexes=[django_expression_index.ExpressionIndex(expressions=[Lower('surface')])]
    
    def __str__(self):
        return self.surface

class LexemTail(models.Model):
    lexem=models.ForeignKey(Lexem, on_delete=models.CASCADE, 
                            related_name="tails", related_query_name='tail')
    surface=models.CharField(max_length=255, db_index=True)
    
    class Meta:
        indexes=[django_expression_index.ExpressionIndex(expressions=[Lower('surface')])]
        unique_together=('lexem','surface')
    
    def __str__(self):
        return self.surface

@receiver(post_save, sender=Lexem)
def update_lexem_tail(instance, **kwargs):
    instance.tails.all().delete()
    for i in range(len(instance.surface)):
        instance.tails.create(surface=instance.surface[i:])


models.CharField.register_lookup(Lower)

class Index(models.Model):
    object_field="object"
    objects=IndexManager()

    search_template=None
    
    class Meta:
        abstract=True
    
    def prepare_text(self):
        return self.tokenize(self.rendered_text)
    
    @classmethod
    def tokenize(cls, text):
        return text.split()
    
    @classmethod
    def parse_query(cls, query):
        lookup = 'surface'
        if query.islower():
            lookup +="__lower"
        tokens=list(cls.tokenize(query))
        if len(tokens)==1:
            return [models.Q(occurrence__lexem__in=Lexem.objects.filter(
                tail__in=LexemTail.objects.filter(**{
                    lookup+"__gte":tokens[0],
                    lookup+"__lt":tokens[0]+chr(0x10FFFF)})))]
            
        return [models.Q(occurrence__lexem__in=Lexem.objects.filter(**{lookup:token}))
                for token in tokens]
    
    @cached_property
    def rendered_text(self):
        return render_to_string(self.search_template, 
                                {self.object_field:getattr(self, self.object_field)})
        
    @cached_property
    def indexed_text(self):
        return " ".join(self.occurrences.values_list("lexem__surface", flat=True))
        
    @classmethod
    def get_index_queryset(cls):
        return cls.objects.target_model._meta.default_manager.all()
