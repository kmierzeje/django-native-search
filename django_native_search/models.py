import logging
import re
from django.db import models
from django.db.models.functions import Lower
import django_expression_index

from django.template.loader import render_to_string
from django.utils.functional import cached_property
from django.db.models.signals import post_save
from django.dispatch import receiver

from .manager import IndexEntryManager, IndexManager
from django.utils.safestring import mark_safe
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django_native_search.fields import OccurrencesField


MIN_TAIL_LEN=getattr(settings,"SEARCH_MIN_SUBSTR_LENGTH", 2)
MAX_TAIL_COUNT_IN_QUERY=getattr(settings, "SEARCH_MAX_SUBTSTR_COUNT_IN_QUERY", 300)
MAX_EXCERPT_FRAGMENTS=getattr(settings, "SEARCH_MAX_EXCERPT_FRAGMENTS", 5)
EXCERPT_FRAGMENT_START_OFFSET=getattr(settings, "SEARCH_EXCERPT_FRAGMENT_START_OFFSET", -3)
EXCERPT_FRAGMENT_END_OFFSET=getattr(settings, "SEARCH_EXCERPT_FRAGMENT_END_OFFSET", 6)
EXCERPT_ADDITONAL_CONTEXT_FACTOR=getattr(settings, "SEARCH_EXCERPT_ADDITONAL_CONTEXT_FACTOR", 2)

logger=logging.getLogger(__name__)

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
        tail=instance.surface[i:]
        if len(tail)>MIN_TAIL_LEN:
            instance.tails.create(surface=tail)


models.CharField.register_lookup(Lower)

class Token(str):
    pass

class IndexEntry(models.Model):
    length=models.PositiveIntegerField(editable=False)
    occurrences=OccurrencesField(query_name="occurrence")
    
    object_field="object"
    objects=IndexEntryManager()

    search_template=None
    
    class Meta:
        abstract=True
        
    
    def save(self, force_insert=False, force_update=False, using=None, 
        update_fields=None):
        logger.info(f"Indexing {self}...")
        self.length=len(self.tokens)
        super().save(force_insert=force_insert, 
                            force_update=force_update, 
                            using=using, 
                            update_fields=update_fields)
    
    @cached_property
    def tokens(self):
        return list(self.prepare_text())
    
    def prepare_text(self):
        return self.tokenize(self.rendered_text)
    
    token_pattern = re.compile(r'[^\s"]+')
    quote = '"'
    
    @classmethod
    def tokenize(cls, text):
        i=0
        sticky=False
        while True:
            res=cls.token_pattern.search(text, i)
            if not res:
                break
            
            token = Token(res.group(0))
            token.prefix=re.sub(r"\s+"," ",text[i:res.start()])
            if cls.quote:
                quotes=token.prefix.count(cls.quote)
                if sticky and quotes>0:
                    sticky = False
                    quotes-=1
                
                token.sticky=sticky
                if quotes%2>0:
                    sticky=not sticky
            i=res.end()
            yield token
    
    @classmethod
    def parse_query(cls, query):
        lookup = 'surface'
        if query.islower():
            lookup +="__lower"
        tokens=list(cls.tokenize(query))
        if len(tokens)==1 and len(tokens[0])>MIN_TAIL_LEN:
            tail_q=LexemTail.objects.filter(**{
                lookup+"__gte":tokens[0],
                lookup+"__lt":tokens[0]+chr(0x10FFFF)})
            if tail_q.count()<=MAX_TAIL_COUNT_IN_QUERY:
                return [models.Q(lexem__in=Lexem.objects.filter(
                    tail__in=tail_q))]
        
        query=[]
        for token in tokens:
            condition=models.Q(lexem__in=Lexem.objects.filter(**{lookup:token}))
            if query:
                condition.sticky=getattr(token,'sticky',False)
            query.append(condition)
        return query
        
    @property
    def excerpt(self):
        matches=getattr(self, 'matches', None)
        if not matches:
            return ""
        
        best_matches={matches[0].lexem_id:(matches[0], 0xFFFF)}
        last_match=matches[0]
        for m in matches[1:]:
            rank=m.position-last_match.position
            last=best_matches.get(last_match.lexem_id)
            if not last or rank<last[1]:
                best_matches[last_match.lexem_id]=last_match, rank
            last_match=m
        
        best_matches=set([m[0] for m in list(best_matches.values())[:MAX_EXCERPT_FRAGMENTS]])
        
        i=1
        while len(best_matches)<MAX_EXCERPT_FRAGMENTS and i<len(matches):
            best_matches.add(matches[i])
            i+=1
            
        additional_context=(MAX_EXCERPT_FRAGMENTS-len(best_matches))*EXCERPT_ADDITONAL_CONTEXT_FACTOR
        
        words=self.occurrences.filter(models.Q(*[
            models.Q(position__gt=match.position+EXCERPT_FRAGMENT_START_OFFSET-additional_context,
                     position__lt=match.position+EXCERPT_FRAGMENT_END_OFFSET+additional_context)
            for match in best_matches], _connector=models.Q.OR))
        
        highlight=set([m.position for m in matches])
        excerpt=""
        pos=-1
        for word in words.select_related('lexem'):
            if word.position>pos+1:
                excerpt+="..."
            if pos>0:
                excerpt+=word.prefix
            
            if word.position in highlight:
                excerpt+= self.highlight(word)
            else:
                excerpt+=word.lexem.surface
            pos=word.position+1
        if pos<self.length:
            excerpt+="..."
        return mark_safe(excerpt)
    
    def highlight(self, word):
        return f"<em>{word.lexem.surface}</em>"
    
    @cached_property
    def rendered_text(self):
        return render_to_string(self.search_template, 
                                {self.object_field:getattr(self, self.object_field)})
        
    @cached_property
    def indexed_text(self):
        return "".join([o.prefix+o.lexem.surface for o in self.occurrences.select_related('lexem')])
        
    @classmethod
    def get_index_queryset(cls):
        return cls.objects.target_model._meta.default_manager.all()
    

class Index(ContentType):
    objects = IndexManager()
    class Meta:
        proxy=True
        verbose_name_plural="indexes"
        
    def occurrences(self):
        return self.model_class().objects.aggregate(models.Count('occurrence'))['occurrence__count']
    
    def entries(self):
        return self.model_class().objects.all().count()
