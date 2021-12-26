import logging
import re
from html import escape
from django.db import models
from django.db.models.functions import Lower
import django_expression_index

from django.template.loader import render_to_string
from django.utils.functional import cached_property

from .manager import IndexEntryManager, IndexManager
from django.utils.safestring import mark_safe
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django_native_search.fields import OccurrencesField
from django.db.models.functions.text import Length


MIN_SUBSTR_LEN=getattr(settings,"SEARCH_MIN_SUBSTR_LENGTH", 2)
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
            if not sticky and token == text and len(token) >= MIN_SUBSTR_LEN:
                token.lookup = "contains"
            yield token
    
    @classmethod
    def parse_query(cls, query):
        lookup = 'surface'
        if query.islower():
            lookup +="__lower"
        tokens=list(cls.tokenize(query))
        
        query=[]
        for token in tokens:
            token.lookup = lookup + "__" + getattr(token,"lookup", "exact")
            lqs = Lexem.objects.filter(**{token.lookup: token})
            if token.lookup.endswith("__contains"):
                lqs=lqs.order_by(Length("surface"))[:20000]
            condition=models.Q(lexem__in=lqs)
            condition.token = token
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
        
        return self.build_excerpt(words, matches)
    
    def build_excerpt(self, words, matches):
        highlight={m.position:m for m in matches}
        excerpt=""
        pos=-1
        for word in words.select_related('lexem'):
            if word.position>pos+1:
                excerpt+="..."
            if pos>0:
                excerpt+=escape(word.prefix)
            
            matched = highlight.get(word.position)
            if matched:
                matched.lexem = word.lexem
                excerpt+= self.highlight(matched)
            else:
                excerpt+=self.to_html(word.lexem.surface)
            pos=word.position+1
        if pos<self.length:
            excerpt+="..."
        return mark_safe(excerpt)
    
    def highlight(self, word):
        if word.token.lookup.endswith("contains"):
            flags = re.IGNORECASE if "__lower__" in word.token.lookup else 0
            pattern = re.compile("(.*?)(("+re.escape(word.token)+")|$)", flags)
            return "".join([self.to_html(part[0]) + self.to_html(part[1], True) 
                            for part in pattern.findall(word.lexem.surface)])
        return self.to_html(word.lexem.surface, True)

    def to_html(self, surface, highlight=False):
        if not surface:
            return ""
        surface=escape(surface)
        if highlight:
            surface = f"<em>{surface}</em>"
        return surface
    
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
