import copy
from django.core.exceptions import FieldDoesNotExist
from django.db.models import (F, Value, Min, OuterRef, Count, FloatField, QuerySet, Q, Prefetch,
                              ExpressionWrapper)
from django.db.models.manager import BaseManager
from django.db.models.functions import Abs
from .fields import OccurrencesField

class SearchQuerySet(QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model=model, query=query, using=using, hints=hints)
        self.search_conditions=[]
        
    def search(self, query):
        ranking=self
        for q in self.model.parse_query(query):
            ranking=ranking.annotate_rank(q)
        results=self.annotate(rank=ranking.filter(pk=OuterRef('pk')).values('rank'))
        results.search_conditions=ranking.search_conditions[:]
        return results.filter(rank__isnull=False).order_by('rank')
        
    def annotate_rank(self, q):
        
        def prefix_lookups(q):
            if isinstance(q, tuple):
                return "occurrence__"+q[0], q[1]
            for i,c in enumerate(q.children):
                q.children[i]=prefix_lookups(c)
            return q
        
        i=len(self.search_conditions)
        ranking=self.filter(prefix_lookups(copy.deepcopy(q)))
        ranking=ranking.annotate(**{f"p{i}":F('occurrence__position')})
        if i==0:
            ranking=ranking.annotate(d0=Value(1, output_field=FloatField()))
        else:
            ranking=ranking.annotate(
                **{f"d{i}":Abs(F(f"p{i}")-F(f'p{i-1}')-1.0, output_field=FloatField())+F(f"d{i-1}")})
        
        ranking=ranking.annotate(
            rank=ExpressionWrapper(Min(f"d{i}")*F("length")/Count("*"), output_field=FloatField()))
        
        ranking.search_conditions.append(q)
        return ranking
    
    def prefetch_matches(self):
        qs=self.model.occurrences.filter(Q(*self.search_conditions, _connector=Q.OR))
        return self.prefetch_related(Prefetch("occurrences", 
                                  queryset=qs,
                                  to_attr="matches"))
    
    def _clone(self):
        c = super()._clone()
        c.search_conditions=self.search_conditions[:]
        return c


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
            
    

