from django.db import models
from django.db.models.functions import Lower
from django.db.backends.utils import names_digest, split_identifier

from django.template.loader import render_to_string
from .manager import IndexManager
from django.utils.functional import cached_property


class ExpressionIndex(models.Index):
    def __init__(self, *, expressions=(), name=None, db_tablespace=None, opclasses=(), condition=None):
        super().__init__(fields=[str(e) for e in expressions], 
                         name=name, db_tablespace=db_tablespace, 
                         opclasses=opclasses, condition=condition)
        self.expressions=expressions
    
    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        kwargs.pop('fields')
        kwargs['expressions'] = self.expressions
        return path, args, kwargs
    
    def set_name_with_model(self, model):
        self.fields_orders=[(model._meta.pk.name,'')]
        _, table_name = split_identifier(model._meta.db_table)
        digest=names_digest(*self.fields, length=6)
        self.name=f"{table_name[:19]}_{digest}_{self.suffix}"
        
    def create_sql(self, model, schema_editor, using='', **kwargs):
        
        class Descriptor:
            db_tablespace=''
            def __init__(self, expression):
                self.column=str(expression)
        
        col_suffixes = [''] * len(self.expressions)
        condition = self._get_condition_sql(model, schema_editor)
        statement= schema_editor._create_index_sql(
            model, [Descriptor(e) for e in self.expressions], 
            name=self.name, using=using, db_tablespace=self.db_tablespace,
            col_suffixes=col_suffixes, opclasses=self.opclasses, condition=condition,
            **kwargs,
        )
        
        compiler=model._meta.default_manager.all().query.get_compiler(connection=schema_editor.connection)
        statement.parts['columns'] = ", ".join(
            [self.compile_expression(e, compiler) for e in self.expressions])
        return statement
    
    def compile_expression(self, expression, compiler):
        expression=expression.resolve_expression(compiler.query, allow_joins=False)
        sql, params=expression.as_sql(compiler, compiler.connection)
        return sql % params

class Lexem(models.Model):
    surface=models.CharField(max_length=255, db_index=True, unique=True)
    
    class Meta:
        indexes=[ExpressionIndex(expressions=[Lower('surface')])]
    
    def __str__(self):
        return self.surface


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
            return [models.Q(occurrence__lexem__in=Lexem.objects.filter(**{
                lookup+"__gte":tokens[0],
                lookup+"__lt":tokens[0]+chr(0x10FFFF)}))]
            
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
