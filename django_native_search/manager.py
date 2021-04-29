import copy
from django.core.exceptions import FieldDoesNotExist
from django.db.models import (F, Value, Min, OuterRef, Count, FloatField, QuerySet, Q, Prefetch,
                              ExpressionWrapper)
from django.db.models.manager import BaseManager, Manager
from django.db.models.functions import Abs
from django.conf import settings
from .fields import OccurrencesField
import logging
from functools import cache



MAX_RANKING_KEYWORDS_COUNT=getattr(settings,"SEARCH_MAX_RANKING_KEYWORDS_COUNT", 3)

logger=logging.getLogger(__name__)


def prefix_lookups(q, prefix):
    if isinstance(q, tuple):
        return prefix+q[0], q[1]
    for i,c in enumerate(q.children):
        q.children[i]=prefix_lookups(c, prefix)
    return q

def apply_lexem_filter(qs, q, sticky, prefix):
    if sticky:
        qs=qs.annotate(p=F(prefix+'position'))
        q=q&Q(position=F('p')+1)
    
    return qs.filter(prefix_lookups(copy.deepcopy(q), prefix))


class SearchQuerySet(QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model=model, query=query, using=using, hints=hints)
        self.search_conditions=[]
    
    @cache
    def count(self):
        cloned=self._clone()
        if cloned.query.annotation_select.pop("rank", None):
            return cloned.count()
        else:
            return super().count()
        
    def apply_filter(self, q, sticky=False):
        return apply_lexem_filter(self, q, sticky, "occurrence__")
    
    def search(self, query):
        ranking=self
        filtered=self
        conditions=self.model.parse_query(query)
        for q in conditions:
            sticky=getattr(q,'sticky', None)
            if not sticky:
                ranking=ranking.annotate_rank(q)
            filtered=filtered.apply_filter(q, sticky)
        
        if filtered is self:
            return self
        
        results=self.annotate(rank=ranking.filter(pk=OuterRef('pk')).values('rank'))
        results.search_conditions=conditions
        results=results.filter(pk__in=filtered)
        return results.order_by('rank')
    
    def annotate_rank(self, q):
        i=len(self.search_conditions)
        if i>=MAX_RANKING_KEYWORDS_COUNT:
            self.search_conditions.append(q)
            return self
        ranking=self.apply_filter(q)
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

class IndexManager(Manager):
    root_indexentry_models=[]
    
    def get_queryset(self):
        qs=super().get_queryset()
        return qs.filter(Q(*[Q(app_label=model._meta.app_label, model = model._meta.model_name)
                            for model in self.indexentry_models], _connector=Q.OR))

    @property
    def indexentry_models(self):
        for model in self.root_indexentry_models:
            for m in self.iter_descendants(model):
                yield m
    
    def iter_descendants(self, cls):
        yield cls
        for child in cls.__subclasses__():
            for m in self.iter_descendants(child):
                yield m
    
    @classmethod
    def register(cls, model):
        cls.root_indexentry_models.append(model)


class IndexEntryManager(BaseManager.from_queryset(SearchQuerySet)):
    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        OccurrencesField(query_name="occurrence").contribute_to_class(cls, "occurrences", **kwargs)
        IndexManager.register(cls)

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
    
    def refresh(self, objects, break_on_failure=False):
        for obj in objects:
            try:
                self.get_or_prepare(obj).save()
            except Exception:
                logger.exception(f"Exception raised when updating index for '{obj}'")
                if break_on_failure:
                    raise
    
    def rebuild(self):
        if self.target_model:
            return self.refresh(self.model.get_index_queryset())
            
        for subcls in self.model.__subclasses__():
            subcls._meta.default_manager.rebuild()
            
    
