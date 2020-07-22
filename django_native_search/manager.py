from django.core.exceptions import FieldDoesNotExist

from django.db.models import F, Value, Min, OuterRef, Count, FloatField, QuerySet
from django.db.models.manager import BaseManager
from django.db.models.functions import Abs
from .fields import OccurrencesField

class SearchQuerySet(QuerySet):
    def search(self, query):
        results=self
        for q in self.model.parse_query(query):
            results=results.annotate_rank(q)
        results=self.annotate(rank=results.filter(pk=OuterRef('pk')).values('rank'))
        return results.filter(rank__isnull=False).order_by('rank')
        
    def annotate_rank(self, q):
        i=getattr(self,"_i", 0)
        ranking=self.filter(q).annotate(**{f"p{i}":F('occurrence__position')})
        if i==0:
            ranking=ranking.annotate(d0=Value(1, output_field=FloatField()))
        else:
            ranking=ranking.annotate(
                **{f"d{i}":Abs(F(f"p{i}")-F(f'p{i-1}'), output_field=FloatField())+F(f"d{i-1}")})
        
        ranking=ranking.annotate(
            rank=Min(f"d{i}", output_field=FloatField())/Count("*", output_field=FloatField()))
        
        ranking._i=i+1
        return ranking


class IndexManager(BaseManager.from_queryset(SearchQuerySet)):
    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        OccurrencesField(query_name="occurrence").contribute_to_class(cls, "occurrences", **kwargs)

    @property
    def target_model(self):
        try:
            return self.model._meta.get_field(self.model.object_field).remote_field.model
        except FieldDoesNotExist:
            return None
        
    def get_or_prepare(self, obj):
        model=self.get_index_model(obj._meta.model)
        if not model:
            raise RuntimeError(f"Index for {obj._meta.model_name} is not configured.")
        try:
            indexed=model._meta.default_manager.get(**{model.object_field:obj})
            setattr(indexed,model.object_field, obj)
            return indexed
        except model.DoesNotExist:
            return model(object=obj)
    
    def get_index_model(self, model):
        if model == self.target_model:
            return self.model
        
        for subcls in self.model.__subclasses__():
            index = subcls._meta.default_manager.get_index_model(model)
            if index:
                return index
        return None
    
    def refresh(self, objects):
        for obj in objects:
            self.get_or_prepare(obj).save()
    
    def rebuild(self):
        if self.target_model:
            return self.refresh(self.model.get_index_queryset())
            
        for subcls in self.model.__subclasses__():
            subcls._meta.default_manager.rebuild()
            
    

