from django.db import migrations
from django_native_search.fields import OccurrencesField

class OccurrencesAddPrefixField(migrations.AlterField):
    def __init__(self, model_name):
        super().__init__(model_name=model_name,
            name='occurrences',
            field=OccurrencesField(query_name="occurrence"))
        
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        from_model=from_state.apps.get_model(app_label, self.model_name)
        from_field = from_model._meta.get_field(self.name)
        through=from_field.remote_field.through
        schema_editor.add_field(through, through._meta.get_field('prefix'))
