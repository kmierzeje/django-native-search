import copy
from django.core.exceptions import FieldDoesNotExist
from django.db.models import (F, Value, Min, Count, FloatField, QuerySet, Q, Prefetch,
                              OuterRef, ExpressionWrapper)
from django.db.models.manager import BaseManager, Manager
from django.db.models.functions import Abs
from django.conf import settings
import logging
from functools import cache
from django.db.models.expressions import When, Case

MAX_RANKING_KEYWORDS = getattr(settings,"SEARCH_MAX_RANKING_KEYWORDS_COUNT", 3)

logger=logging.getLogger(__name__)


def prefix_lookups(q, prefix):
    if isinstance(q, tuple):
        return prefix+q[0], q[1]
    for i,c in enumerate(q.children):
        q.children[i]=prefix_lookups(c, prefix)
    return q


class SearchQuerySet(QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model=model, query=query, using=using, hints=hints)
        self.search_conditions=[]
    
    @cache
    def count(self):
        return self.values('pk').aggregate(c=Count("*"))['c']
        
    def apply_filter(self, q):
        filtered = self.filter(prefix_lookups(copy.deepcopy(q), "occurrence__"))
        filtered.search_conditions.append(q)
        return filtered
    
    def search_one(self, condition):
        return self.apply_filter(condition).distinct().annotate_rank().order_by("rank")
    
    def search(self, query):
        ranking=self
        filtered=self
        conditions=self.model.parse_query(query)
        if len(conditions) == 1:
            return self.search_one(conditions[0])
        
        filter_by_ranking = False
        for i, q in enumerate(conditions):
            if filtered is not self:
                filtered = self.filter(pk__in=filtered.all())
            filtered = filtered.apply_filter(q)
            
            sticky = getattr(q.token,'sticky', None)
            
            if not sticky and i>=MAX_RANKING_KEYWORDS-1:
                if filter_by_ranking:
                    filtered = filtered.filter(pk__in=ranking.values("pk"))
                ranking=filtered.carry_annotation(ranking, "rank")
                continue
            ranking=ranking.apply_filter(q).annotate_rank()
            if sticky:
                ranking=ranking.filter(d=1)
                filter_by_ranking = True
        
        if filtered is self:
            return self
        
        if filter_by_ranking:
            filtered=filtered.filter(pk__in=ranking.values("pk"))
        
        results = self.filter(pk__in=filtered)
        results = results.carry_annotation(ranking, "rank")
        results.search_conditions=conditions
        return results.order_by("rank")
    
    def carry_annotation(self, qs, src, dst=None):
        return self.annotate(**{dst or src:qs.filter(pk=OuterRef("pk")).values(src)[:1]})

    def annotate_rank(self):
        ranking=self
        if "p" in ranking.query.annotations:
            ranking=ranking.alias(
                d=F("occurrence__position")-F("p"),
                dsum=Abs(F('d')-1.0, output_field=FloatField())+F("dsum"))
        else:
            ranking=ranking.alias(
                dsum=Value(1, output_field=FloatField()))
            
        ranking=ranking.annotate(rank=ExpressionWrapper(
            Min("dsum")*F("length")/Count("*"), output_field=FloatField()))

        ranking=ranking.alias(p=F("occurrence__position"))
        return ranking

    def prefetch_matches(self):
        conditions=[]
        tokens=[]
        for condition in self.search_conditions:
            conditions.append(When(condition, then=Value(len(tokens))))
            tokens.append(condition.token)
        qs=self.model.occurrences.annotate(token=Case(*conditions)).filter(token__isnull=False)
        class Decor(qs.__class__):
            def __iter__(self):
                for obj in super().__iter__():
                    if isinstance(obj.token, int):
                        obj.token=tokens[obj.token]
                    yield obj
        qs.__class__ = Decor
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
            
    
